from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from config import settings
from core.domain import PaymentDraft, SettlementResult, SubLotDraft, TrustEvent, VerificationOutput
from core.exceptions import InvalidStateTransitionError
from core.settlement.calculator import SettlementCalculator, SettlementConfig
from core.trust.scorer import TrustScorer, TrustScorerConfig
from db.repositories.buyer_payment_repository import BuyerPaymentRepository
from db.repositories.notification_repository import NotificationRepository
from db.repositories.order_repository import OrderRepository
from db.repositories.payment_repository import PaymentRepository
from db.repositories.sublot_repository import SublotRepository
from db.repositories.trust_repository import TrustRepository
from db.repositories.verification_repository import VerificationRepository
from db.repositories.workshop_repository import WorkshopRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def conn():
    try:
        connection = await asyncpg.connect(dsn=settings.database_url)
    except OSError as exc:
        pytest.skip(f"Postgres not reachable at {settings.database_url}: {exc}")
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text",
    )
    tx = connection.transaction()
    await tx.start()
    try:
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


@pytest.mark.anyio
async def test_full_order_lifecycle(conn: asyncpg.Connection) -> None:
    orders = OrderRepository(conn)
    sublots = SublotRepository(conn)
    workshops = WorkshopRepository(conn)
    verifications = VerificationRepository(conn)
    payments = PaymentRepository(conn)
    notifications = NotificationRepository(conn)
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.500))
    trust = TrustRepository(conn, scorer)
    order_id = await orders.create(
        buyer_ref="integration-test-buyer",
        product_type="jute-door-mat",
        total_qty=100,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
    )
    assert order_id > 0

    row = await orders.get(order_id)
    assert row["status"] == "PENDING"
    assert row["buyer_ref"] == "integration-test-buyer"

    by_corr = await orders.get_by_correlation_id(str(row["correlation_id"]))
    assert by_corr["order_id"] == order_id
    await orders.transition_status(order_id, "PENDING", "ALLOCATING")
    assert (await orders.get(order_id))["status"] == "ALLOCATING"

    with pytest.raises(InvalidStateTransitionError):
        await orders.transition_status(order_id, "ALLOCATING", "CLOSED")

    with pytest.raises(InvalidStateTransitionError):
        await orders.transition_status(order_id, "PENDING", "ALLOCATING")

    await orders.transition_status(order_id, "ALLOCATING", "ALLOCATED")
    assert (await orders.get(order_id))["status"] == "ALLOCATED"
    bids = await workshops.list_bids("jute-door-mat")
    ws_ids = {b.workshop_id for b in bids}
    assert ws_ids == {1, 2, 3, 4, 5, 6}

    ws1_before = next(b for b in bids if b.workshop_id == 1)
    await workshops.reserve_capacity(1, "jute-door-mat", 40)
    await workshops.reserve_capacity(2, "jute-door-mat", 60)

    bids_after = await workshops.list_bids("jute-door-mat")
    ws1_after = next(b for b in bids_after if b.workshop_id == 1)
    assert ws1_after.reserved_qty == ws1_before.reserved_qty + 40

    drafts = [
        SubLotDraft(order_id=order_id, workshop_id=1, qty_assigned=40, cost_per_unit=Decimal("95.00")),
        SubLotDraft(order_id=order_id, workshop_id=2, qty_assigned=60, cost_per_unit=Decimal("88.00")),
    ]
    sublot_ids = await sublots.create_batch(drafts, conn=conn)
    assert len(sublot_ids) == 2

    for_order = await sublots.list_for_order(order_id)
    assert {s.workshop_id for s in for_order} == {1, 2}
    assert sum(s.qty_assigned for s in for_order) == 100

    await orders.transition_status(order_id, "ALLOCATED", "IN_PRODUCTION")
    await notifications.create(1, order_id, sublot_ids[0], "jute-door-mat", 40)
    await notifications.create(1, order_id, sublot_ids[0], "jute-door-mat", 40)
    ws1_notifications = await notifications.list_for_workshop(1)
    assert len([n for n in ws1_notifications if n["sublot_id"] == sublot_ids[0]]) == 1
    await sublots.mark_delivered(sublot_ids[0], delivered_qty=40)
    await sublots.mark_delivered(sublot_ids[1], delivered_qty=55)

    delivered = await sublots.get(sublot_ids[1])
    assert delivered.status == "DELIVERED"
    assert delivered.delivered_qty == 55

    await sublots.mark_delivered(sublot_ids[1], delivered_qty=999)
    assert (await sublots.get(sublot_ids[1])).delivered_qty == 55

    assert await sublots.all_delivered_or_further(order_id) is True
    assert await sublots.all_terminal(order_id) is False

    await orders.transition_status(order_id, "IN_PRODUCTION", "VERIFYING")
    await verifications.save(
        sublot_ids[0],
        VerificationOutput(verdict="OK", fault_party="none", confidence=0.95, explanation="Matches spec."),
        photo_path=None,
    )
    await verifications.save(
        sublot_ids[1],
        VerificationOutput(
            verdict="DEFECT", fault_party="workshop", confidence=0.88,
            explanation="Stitching defect on 5 units.",
        ),
        photo_path="/uploads/defect.jpg",
    )
    await verifications.save(
        sublot_ids[0],
        VerificationOutput(verdict="DEFECT", fault_party="workshop", confidence=0.5, explanation="should not stick"),
        photo_path=None,
    )
    v0 = await verifications.get(sublot_ids[0])
    assert v0.verdict == "OK"

    for_order_v = await verifications.get_for_order(order_id)
    assert for_order_v[sublot_ids[1]].verdict == "DEFECT"

    await sublots.transition_status(sublot_ids[0], "VERIFIED")
    await sublots.transition_status(sublot_ids[1], "VERIFIED")
    assert await sublots.all_terminal(order_id) is True
    now = datetime.now(timezone.utc)
    await trust.append_event(TrustEvent(
        workshop_id=1, sublot_id=sublot_ids[0], on_time=True, defect_found=False,
        fault_party="none", created_at=now,
    ))
    await trust.append_event(TrustEvent(
        workshop_id=2, sublot_id=sublot_ids[1], on_time=True, defect_found=True,
        fault_party="workshop", created_at=now,
    ))

    events_ws2 = await trust.get_recent_events(2, limit=10)
    assert len(events_ws2) >= 1
    assert events_ws2[0].defect_found is True

    cache_row = await conn.fetchrow(
        "SELECT score, grade FROM trust_score_cache WHERE workshop_id = $1", 2
    )
    assert cache_row is not None
    assert 0.0 <= float(cache_row["score"]) <= 1.0
    assert cache_row["grade"] in {"A", "B", "C", "D"}

    explanations = await trust.get_recent_explanations(2, window_size=10)
    assert isinstance(explanations, list)
    ws1_base = Decimal("40") * Decimal("95.00")
    ws2_base = Decimal("55") * Decimal("88.00")
    settlement = SettlementResult(
        payments=[
            PaymentDraft(
                workshop_id=1, base_amount=ws1_base, penalty=Decimal("0.00"),
                net_amount=ws1_base, buyer_billable_amount=ws1_base,
            ),
            PaymentDraft(
                workshop_id=2, base_amount=ws2_base,
                penalty=ws2_base,
                net_amount=Decimal("0.00"),
                buyer_billable_amount=Decimal("0.00"),
            ),
        ],
        buyer_base=ws1_base,
        platform_fee=(ws1_base) * Decimal("0.05"),
        buyer_total=(ws1_base) * Decimal("1.05"),
    )
    await payments.save_settlement(order_id, [sublot_ids[0], sublot_ids[1]], settlement)
    await payments.save_settlement(order_id, [sublot_ids[0], sublot_ids[1]], settlement)

    saved_payments = await payments.get_for_order(order_id)
    assert len(saved_payments) == 2
    net_by_ws = {p["workshop_id"]: p["net_amount"] for p in saved_payments}
    assert net_by_ws[1] == ws1_base
    assert net_by_ws[2] == Decimal("0.00")

    await orders.transition_status(order_id, "VERIFYING", "SETTLING")
    await orders.transition_status(order_id, "SETTLING", "CLOSED")
    assert (await orders.get(order_id))["status"] == "CLOSED"

    with pytest.raises(InvalidStateTransitionError):
        await orders.transition_status(order_id, "CLOSED", "SETTLING")


@pytest.mark.anyio
async def test_cancellation_rules(conn: asyncpg.Connection) -> None:
    orders = OrderRepository(conn)

    order_id = await orders.create(
        buyer_ref="cancel-test-buyer",
        product_type="jute-door-mat",
        total_qty=50,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
    )

    await orders.cancel(order_id)
    assert (await orders.get(order_id))["status"] == "CANCELLED"

    order_id_2 = await orders.create(
        buyer_ref="cancel-test-buyer-2",
        product_type="jute-door-mat",
        total_qty=50,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
    )
    await orders.transition_status(order_id_2, "PENDING", "ALLOCATING")

    with pytest.raises(InvalidStateTransitionError):
        await orders.cancel(order_id_2)

    await orders.transition_status(order_id_2, "ALLOCATING", "ALLOCATED")

    sublots = SublotRepository(conn)
    sublot_ids = await sublots.create_batch([
        SubLotDraft(order_id=order_id_2, workshop_id=1, qty_assigned=20, cost_per_unit=Decimal("50.00")),
    ], conn=conn)
    assert (await sublots.get(sublot_ids[0])).status == "ASSIGNED"

    await orders.cancel(order_id_2)
    assert (await orders.get(order_id_2))["status"] == "CANCELLED"

    await sublots.cancel_for_order(order_id_2)
    assert (await sublots.get(sublot_ids[0])).status == "CANCELLED"

    with pytest.raises(InvalidStateTransitionError):
        await orders.cancel(order_id_2)


@pytest.mark.anyio
async def test_schema_constraints_enforced(conn: asyncpg.Connection) -> None:
    order_id = await conn.fetchval(
        """
        INSERT INTO orders (buyer_ref, product_type, total_qty, quality_min, deadline,
                             factory_fallback_cost, factory_workshop_id)
        VALUES ('constraint-test', 'jute-door-mat', 10, 2, '2026-12-31', 180.00, 99)
        RETURNING order_id
        """
    )
    sublot_id = await conn.fetchval(
        """
        INSERT INTO sublots (order_id, workshop_id, qty_assigned, cost_per_unit)
        VALUES ($1, 1, 10, 95.00) RETURNING sublot_id
        """,
        order_id,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "UPDATE sublots SET delivered_qty = 999 WHERE sublot_id = $1", sublot_id
            )

    with pytest.raises(asyncpg.CheckViolationError):
        async with conn.transaction():
            await conn.execute(
                "UPDATE workshop_capacity SET reserved_qty = available_qty + 1 "
                "WHERE workshop_id = 1 AND product_type = 'jute-door-mat'"
            )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO trust_events (workshop_id, sublot_id, event_type, on_time, defect_found, fault_party)
                VALUES (1, 999999999, 'DELIVERY_ON_TIME', TRUE, FALSE, 'none')
                """
            )

    await conn.execute(
        """
        INSERT INTO verification_results (sublot_id, verdict, fault_party, confidence, explanation)
        VALUES ($1, 'OK', 'none', 0.9, 'first')
        """,
        sublot_id,
    )
    await conn.execute(
        """
        INSERT INTO verification_results (sublot_id, verdict, fault_party, confidence, explanation)
        VALUES ($1, 'DEFECT', 'workshop', 1.0, 'second')
        """,
        sublot_id,
    )
    verification_rows = await conn.fetch(
        "SELECT verdict FROM verification_results WHERE sublot_id = $1 ORDER BY created_at", sublot_id
    )
    assert [r["verdict"] for r in verification_rows] == ["OK", "DEFECT"]

    await conn.execute(
        """
        INSERT INTO trust_events (workshop_id, sublot_id, event_type, on_time, defect_found, fault_party)
        VALUES (1, $1, 'DELIVERY_ON_TIME', TRUE, FALSE, 'none')
        """,
        sublot_id,
    )
    await conn.execute(
        """
        INSERT INTO trust_events (workshop_id, sublot_id, event_type, on_time, defect_found, fault_party)
        VALUES (1, $1, 'DEFECT_WORKSHOP', TRUE, TRUE, 'workshop')
        """,
        sublot_id,
    )
    trust_event_rows = await conn.fetch(
        "SELECT defect_found FROM trust_events WHERE sublot_id = $1 ORDER BY created_at", sublot_id
    )
    assert [r["defect_found"] for r in trust_event_rows] == [False, True]


@pytest.mark.anyio
async def test_buyer_payments_flow(conn: asyncpg.Connection) -> None:
    orders = OrderRepository(conn)
    buyer_payments = BuyerPaymentRepository(conn)

    order_id = await orders.create(
        buyer_ref="buyer-payments-buyer",
        product_type="jute-door-mat",
        total_qty=100,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
        payment_terms="ADVANCE_PLUS_BALANCE",
    )
    assert (await orders.get(order_id))["payment_terms"] == "ADVANCE_PLUS_BALANCE"

    await buyer_payments.create_advance(order_id, Decimal("5670.00"))
    await buyer_payments.create_advance(order_id, Decimal("9999.00"))  # duplicate, must no-op

    rows = await buyer_payments.get_for_order(order_id)
    assert len(rows) == 1
    assert rows[0]["kind"] == "ADVANCE"
    assert rows[0]["amount"] == Decimal("5670.00")
    assert rows[0]["status"] == "PENDING"
    assert rows[0]["paid_at"] is None

    paid = await buyer_payments.mark_paid(order_id, rows[0]["buyer_payment_id"])
    assert paid["status"] == "PAID"
    assert paid["paid_at"] is not None

    again = await buyer_payments.mark_paid(order_id, rows[0]["buyer_payment_id"])
    assert again is None  # already paid, second attempt is a no-op

    await buyer_payments.create_balance(order_id, Decimal("13230.00"))
    final_rows = await buyer_payments.get_for_order(order_id)
    assert {r["kind"]: r["amount"] for r in final_rows} == {
        "ADVANCE": Decimal("5670.00"),
        "BALANCE": Decimal("13230.00"),
    }

    await buyer_payments.create_refund(order_id, Decimal("5670.00"))
    await buyer_payments.create_refund(order_id, Decimal("9999.00"))  # duplicate, must no-op
    refund_rows = await buyer_payments.get_for_order(order_id)
    assert {r["kind"]: r["amount"] for r in refund_rows} == {
        "ADVANCE": Decimal("5670.00"),
        "BALANCE": Decimal("13230.00"),
        "REFUND": Decimal("5670.00"),
    }


@pytest.mark.anyio
async def test_balance_reconciles_correctly_after_factory_fallback_backfill(
    conn: asyncpg.Connection,
) -> None:
    """A workshop under-delivers, the shortfall gets backfilled to the (pricier)
    factory, and the buyer's ADVANCE_PLUS_BALANCE advance — sized off the
    100%-factory-cost estimate at order placement — must still reconcile
    correctly against the real, mixed workshop+factory settlement total."""
    orders = OrderRepository(conn)
    sublots = SublotRepository(conn)
    buyer_payments = BuyerPaymentRepository(conn)
    calculator = SettlementCalculator(SettlementConfig(
        platform_fee_percentage=Decimal("0.05"),
        penalty_non_delivery_percentage=Decimal("0.20"),
    ))

    order_id = await orders.create(
        buyer_ref="factory-fallback-buyer",
        product_type="jute-door-mat",
        total_qty=100,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
        payment_terms="ADVANCE_PLUS_BALANCE",
    )

    # Advance collected at placement: 100% factory-cost estimate x 5% platform
    # fee x 30% advance = (100 * 180.00 * 1.05) * 0.30 = 5670.00
    await buyer_payments.create_advance(order_id, Decimal("5670.00"))

    # Workshop assigned all 100 units but only delivers 75 (shortfall 25) —
    # the shortfall is backfilled to the factory at its (higher) fallback cost.
    workshop_sublot_id, factory_sublot_id = await sublots.create_batch(
        [
            SubLotDraft(order_id=order_id, workshop_id=1, qty_assigned=100, cost_per_unit=Decimal("90.00")),
            SubLotDraft(order_id=order_id, workshop_id=99, qty_assigned=25, cost_per_unit=Decimal("180.00")),
        ],
        conn=conn,
    )
    await sublots.mark_delivered(workshop_sublot_id, delivered_qty=75)
    await sublots.mark_delivered(factory_sublot_id, delivered_qty=25)
    await sublots.transition_status(workshop_sublot_id, "VERIFIED")
    await sublots.transition_status(factory_sublot_id, "VERIFIED")

    sublot_rows = await sublots.list_for_order(order_id)
    result = calculator.compute(sublot_rows, {})

    # workshop: 75 * 90.00 = 6750.00; factory backfill: 25 * 180.00 = 4500.00
    assert result.buyer_base == Decimal("11250.00")
    assert result.platform_fee == Decimal("562.50")
    assert result.buyer_total == Decimal("11812.50")

    advance_row = (await buyer_payments.get_for_order(order_id))[0]
    balance = calculator.compute_balance_due(result.buyer_total, Decimal(str(advance_row["amount"])))
    assert balance == Decimal("6142.50")

    await buyer_payments.create_balance(order_id, balance)
    final_rows = {r["kind"]: r["amount"] for r in await buyer_payments.get_for_order(order_id)}
    assert final_rows == {"ADVANCE": Decimal("5670.00"), "BALANCE": Decimal("6142.50")}


@pytest.mark.anyio
async def test_workshop_facing_queries(conn: asyncpg.Connection) -> None:
    orders = OrderRepository(conn)
    sublots = SublotRepository(conn)
    workshops = WorkshopRepository(conn)
    capacity_rows = await workshops.list_capacity(1)
    assert any(r["product_type"] == "jute-door-mat" for r in capacity_rows)
    jute_row = next(r for r in capacity_rows if r["product_type"] == "jute-door-mat")
    assert jute_row["available_qty"] >= jute_row["reserved_qty"]
    order_id = await orders.create(
        buyer_ref="workshop-queries-buyer",
        product_type="jute-door-mat",
        total_qty=20,
        quality_min=2,
        deadline=date(2026, 12, 31),
        factory_fallback_cost=Decimal("180.00"),
        factory_workshop_id=99,
    )
    sublot_id = (await sublots.create_batch(
        [SubLotDraft(order_id=order_id, workshop_id=1, qty_assigned=20, cost_per_unit=Decimal("95.00"))],
        conn=conn,
    ))[0]

    assert await sublots.start_production(sublot_id) is True
    assert (await sublots.get(sublot_id)).status == "IN_PRODUCTION"
    assert await sublots.start_production(sublot_id) is False
    limited = await sublots.list_for_workshop(1, limit=1)
    assert len(limited) == 1
    assert limited[0]["sublot_id"] == sublot_id
    await sublots.mark_delivered(sublot_id, delivered_qty=20)
    assert sublot_id not in await sublots.list_delivered_past_grace(grace_seconds=3600)
    assert sublot_id in await sublots.list_delivered_past_grace(grace_seconds=0)
