from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from config import settings
from core.domain import SubLotDraft, VerificationOutput
from core.exceptions import InvalidStateTransitionError, NeedsHumanReviewError
from services.coordinator import OrderCoordinator, build_coordinator

def _make_order_row(
    status: str = "ALLOCATING",
    total_qty: int = 100,
    product_type: str = "kurta",
    quality_min: int = 3,
    deadline: date = date(2026, 8, 1),
    factory_fallback_cost: Decimal = Decimal("150.00"),
    factory_workshop_id: int = 99,
    correlation_id: str = "test-correlation-id",
    payment_terms: str = "PAY_ON_DELIVERY",
) -> dict:
    return {
        "status": status,
        "total_qty": total_qty,
        "product_type": product_type,
        "quality_min": quality_min,
        "deadline": deadline,
        "factory_fallback_cost": factory_fallback_cost,
        "factory_workshop_id": factory_workshop_id,
        "correlation_id": correlation_id,
        "payment_terms": payment_terms,
    }


def _make_sublot_row(
    sublot_id: int = 1,
    order_id: int = 1,
    workshop_id: int = 1,
    status: str = "DELIVERED",
    delivered_qty: int = 50,
    qty_assigned: int = 50,
    delivered_at: datetime | None = datetime(2026, 7, 20, tzinfo=timezone.utc),
) -> MagicMock:
    row = MagicMock()
    row.sublot_id = sublot_id
    row.order_id = order_id
    row.workshop_id = workshop_id
    row.status = status
    row.delivered_qty = delivered_qty
    row.qty_assigned = qty_assigned
    row.delivered_at = delivered_at
    return row


def _make_coordinator(**overrides) -> OrderCoordinator:
    order_repo = AsyncMock()
    order_repo.get.return_value = _make_order_row()
    verification_agent = AsyncMock()
    verification_agent.translate_explanation.return_value = None
    defaults = dict(
        order_repo=order_repo,
        workshop_repo=AsyncMock(),
        sublot_repo=AsyncMock(),
        trust_repo=AsyncMock(),
        verification_repo=AsyncMock(),
        payment_repo=AsyncMock(),
        notification_repo=AsyncMock(),
        allocation_engine=MagicMock(),
        trust_scorer=MagicMock(),
        settlement_calculator=MagicMock(),
        verification_agent=verification_agent,
        notification_gateway=AsyncMock(),
        buyer_payment_repo=AsyncMock(),
    )
    defaults.update(overrides)
    return OrderCoordinator(**defaults)

@pytest.mark.anyio
async def test_on_order_placed_idempotency_skip():
    coord = _make_coordinator()
    coord._orders.transition_status.side_effect = InvalidStateTransitionError("already past PENDING")

    await coord.on_order_placed(order_id=42)

    coord._engine.allocate.assert_not_called()
    coord._sublots.create_batch.assert_not_called()


@pytest.mark.anyio
async def test_on_order_placed_order_not_found_returns_early():
    coord = _make_coordinator()
    coord._orders.transition_status = AsyncMock()
    coord._orders.get = AsyncMock(return_value=None)

    result = await coord.on_order_placed(order_id=5)

    coord._engine.allocate.assert_not_called()
    assert result == []


@pytest.mark.anyio
async def test_on_order_placed_idempotency_skip_returns_empty_list():
    coord = _make_coordinator()
    coord._orders.transition_status.side_effect = InvalidStateTransitionError("already past PENDING")

    result = await coord.on_order_placed(order_id=42)

    assert result == []


@pytest.mark.anyio
async def test_on_order_placed_full_happy_path():
    coord = _make_coordinator()
    coord._orders.transition_status = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row())

    draft = MagicMock()
    draft.workshop_id = 7
    draft.qty_assigned = 100
    coord._engine.allocate = MagicMock(return_value=[draft])
    coord._workshops.list_bids = AsyncMock(return_value=[])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.create_batch = AsyncMock(return_value=[1])

    result = await coord.on_order_placed(order_id=1)

    coord._workshops.reserve_capacity.assert_called_once_with(7, "kurta", 100)
    coord._sublots.create_batch.assert_called_once()
    coord._orders.transition_status.assert_called_with(1, "ALLOCATING", "ALLOCATED")

    assert len(result) == 1
    assert result[0].sublot_id == 1
    assert result[0].order_id == 1
    assert result[0].workshop_id == 7
    assert result[0].product_type == "kurta"
    assert result[0].qty_assigned == 100


@pytest.mark.anyio
async def test_on_order_placed_excludes_factory_from_returned_assignments():
    coord = _make_coordinator()
    coord._orders.transition_status = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(factory_workshop_id=99))

    workshop_draft = MagicMock()
    workshop_draft.workshop_id = 7
    workshop_draft.qty_assigned = 60
    factory_draft = MagicMock()
    factory_draft.workshop_id = 99
    factory_draft.qty_assigned = 40
    coord._engine.allocate = MagicMock(return_value=[workshop_draft, factory_draft])
    coord._workshops.list_bids = AsyncMock(return_value=[])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.create_batch = AsyncMock(return_value=[1, 2])

    result = await coord.on_order_placed(order_id=1)

    assert len(result) == 1
    assert result[0].workshop_id == 7

@pytest.mark.anyio
async def test_on_sublot_assigned_persists_notification():
    coord = _make_coordinator()
    coord._notifications.create = AsyncMock()
    coord._workshops.get_phone = AsyncMock(return_value=None)

    await coord.on_sublot_assigned(
        workshop_id=7, order_id=1, sublot_id=3, product_type="kurta", qty_assigned=100,
    )

    coord._notifications.create.assert_called_once_with(
        workshop_id=7, order_id=1, sublot_id=3, product_type="kurta", qty_assigned=100,
    )

@pytest.mark.anyio
async def test_on_sublot_assigned_pings_workshop_phone_when_registered():
    coord = _make_coordinator()
    coord._notifications.create = AsyncMock()
    coord._workshops.get_phone = AsyncMock(return_value="+919810000007")

    await coord.on_sublot_assigned(
        workshop_id=7, order_id=1, sublot_id=3, product_type="kurta", qty_assigned=100,
    )

    coord._gateway.send.assert_awaited_once()
    phone_arg, message_arg = coord._gateway.send.call_args.args
    assert phone_arg == "+919810000007"
    assert "kurta" in message_arg and "100" in message_arg

@pytest.mark.anyio
async def test_on_sublot_assigned_skips_ping_when_no_phone_registered():
    coord = _make_coordinator()
    coord._notifications.create = AsyncMock()
    coord._workshops.get_phone = AsyncMock(return_value=None)

    await coord.on_sublot_assigned(
        workshop_id=99, order_id=1, sublot_id=3, product_type="kurta", qty_assigned=100,
    )

    coord._gateway.send.assert_not_called()

@pytest.mark.anyio
async def test_on_sublot_delivered_leaves_no_photo_sublot_in_delivered():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=1, workshop_id=7, status="ASSIGNED", qty_assigned=50)
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._sublots.mark_capacity_released = AsyncMock(return_value=False)
    coord._orders.get = AsyncMock(return_value=_make_order_row(product_type="kurta"))
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=50)

    coord._sublots.transition_status.assert_not_called()
    coord._trust.append_event.assert_not_called()

@pytest.mark.anyio
async def test_auto_verify_expired_deliveries_approves_each_expired_sublot():
    coord = _make_coordinator()
    coord._sublots.list_delivered_past_grace = AsyncMock(return_value=[1, 2])
    coord._sublots.get = AsyncMock(side_effect=[
        _make_sublot_row(sublot_id=1, order_id=10, workshop_id=7, status="DELIVERED"),
        _make_sublot_row(sublot_id=2, order_id=11, workshop_id=8, status="DELIVERED"),
    ])
    coord._trust.append_event = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    count = await coord.auto_verify_expired_deliveries()

    assert count == 2
    coord._sublots.transition_status.assert_any_call(1, "VERIFIED")
    coord._sublots.transition_status.assert_any_call(2, "VERIFIED")
    assert coord._trust.append_event.await_count == 2
    coord._sublots.list_delivered_past_grace.assert_awaited_once_with(
        settings.verification_auto_approve_grace_seconds
    )


@pytest.mark.anyio
async def test_auto_verify_expired_deliveries_skips_sublot_that_moved_on_since_listing():
    coord = _make_coordinator()
    coord._sublots.list_delivered_past_grace = AsyncMock(return_value=[1])
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=1, order_id=10, status="VERIFYING")
    )
    coord._trust.append_event = AsyncMock()
    coord._sublots.transition_status = AsyncMock()

    count = await coord.auto_verify_expired_deliveries()

    assert count == 0
    coord._trust.append_event.assert_not_called()
    coord._sublots.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_auto_verify_expired_deliveries_skips_sublot_that_vanished():
    coord = _make_coordinator()
    coord._sublots.list_delivered_past_grace = AsyncMock(return_value=[1])
    coord._sublots.get = AsyncMock(return_value=None)
    coord._trust.append_event = AsyncMock()

    count = await coord.auto_verify_expired_deliveries()

    assert count == 0
    coord._trust.append_event.assert_not_called()


@pytest.mark.anyio
async def test_auto_verify_expired_deliveries_noop_when_nothing_expired():
    coord = _make_coordinator()
    coord._sublots.list_delivered_past_grace = AsyncMock(return_value=[])
    coord._trust.append_event = AsyncMock()

    count = await coord.auto_verify_expired_deliveries()

    assert count == 0
    coord._trust.append_event.assert_not_called()

@pytest.mark.anyio
async def test_on_production_started_transitions_sublot_and_order():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=1, order_id=7, status="ASSIGNED")
    )
    coord._sublots.start_production = AsyncMock(return_value=True)
    coord._orders.get = AsyncMock(return_value=_make_order_row(status="ALLOCATED"))
    coord._orders.transition_status = AsyncMock()

    await coord.on_production_started(sublot_id=1)

    coord._sublots.start_production.assert_awaited_once_with(1)
    coord._orders.transition_status.assert_awaited_once_with(7, "ALLOCATED", "IN_PRODUCTION")


@pytest.mark.anyio
async def test_on_production_started_noop_when_sublot_not_assigned():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=1, order_id=7, status="IN_PRODUCTION")
    )
    coord._sublots.start_production = AsyncMock(return_value=False)
    coord._orders.transition_status = AsyncMock()

    await coord.on_production_started(sublot_id=1)

    coord._orders.transition_status.assert_not_awaited()


@pytest.mark.anyio
async def test_on_production_started_sublot_not_found_logs_and_returns():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=None)
    coord._sublots.start_production = AsyncMock()

    await coord.on_production_started(sublot_id=999)

    coord._sublots.start_production.assert_not_awaited()


@pytest.mark.anyio
async def test_on_production_started_second_sublot_is_safe_noop_via_ensure_in_production():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=2, order_id=7, status="ASSIGNED")
    )
    coord._sublots.start_production = AsyncMock(return_value=True)
    coord._orders.get = AsyncMock(return_value=_make_order_row(status="IN_PRODUCTION"))
    coord._orders.transition_status = AsyncMock()

    await coord.on_production_started(sublot_id=2)

    coord._orders.transition_status.assert_not_awaited()

@pytest.mark.anyio
async def test_spec_ambiguity_fetches_order_row_for_on_time_and_disputes_workshop():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(order_id=10, status="VERIFYING")
    )
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._workshops.increment_spec_disputes = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    output = VerificationOutput(
        verdict="SPEC_AMBIGUITY",
        fault_party="buyer",
        confidence=0.7,
        explanation="Spec too vague.",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    await coord.on_verification_complete(
        sublot_id=1, order_id=10, photo_path="photo.jpg"
    )

    coord._orders.get.assert_called_once_with(10)
    coord._workshops.increment_spec_disputes.assert_called_once_with(1)


@pytest.mark.anyio
async def test_defect_workshop_fault_sets_failed_status():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    output = VerificationOutput(
        verdict="DEFECT",
        fault_party="workshop",
        confidence=0.91,
        explanation="Stitching defect.",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    result = await coord.on_verification_complete(
        sublot_id=2, order_id=5, photo_path="photo.jpg"
    )

    coord._sublots.transition_status.assert_called_once_with(2, "FAILED")
    coord._workshops.increment_spec_disputes.assert_not_called()
    assert result.status == "FAILED"
    assert result.explanation == "Stitching defect."


@pytest.mark.anyio
async def test_low_confidence_defect_flags_needs_human_review_not_failed():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._workshops.increment_spec_disputes = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    output = VerificationOutput(
        verdict="DEFECT",
        fault_party="workshop",
        confidence=0.55,
        explanation="Possible stitching defect, hard to tell from the photo.",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    result = await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    coord._verifications.save.assert_called_once_with(2, output, "photo.jpg", {})
    coord._sublots.transition_status.assert_called_once_with(2, "NEEDS_HUMAN_REVIEW")
    coord._trust.append_event.assert_not_called()
    coord._workshops.increment_spec_disputes.assert_not_called()
    assert result.status == "NEEDS_HUMAN_REVIEW"
    assert result.explanation == "Possible stitching defect, hard to tell from the photo."


@pytest.mark.anyio
async def test_defect_confidence_exactly_at_threshold_is_auto_applied():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    output = VerificationOutput(
        verdict="DEFECT",
        fault_party="workshop",
        confidence=settings.verification_defect_confidence_threshold,
        explanation="Clear stitching defect.",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    result = await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    coord._sublots.transition_status.assert_called_once_with(2, "FAILED")
    coord._trust.append_event.assert_called_once()
    assert result.status == "FAILED"


@pytest.mark.anyio
async def test_low_confidence_ok_verdict_is_still_auto_applied():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    output = VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.40, explanation="Looks fine, low confidence."
    )
    coord._agent.verify = AsyncMock(return_value=output)

    result = await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    coord._sublots.transition_status.assert_called_once_with(2, "VERIFIED")
    coord._trust.append_event.assert_called_once()
    assert result.status == "VERIFIED"


@pytest.mark.anyio
async def test_on_verification_complete_threads_translated_explanations_to_the_caller():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.translate_explanation.return_value = "स्पष्ट सिलाई दोष।"

    output = VerificationOutput(
        verdict="DEFECT", fault_party="workshop", confidence=0.95, explanation="Clear stitching defect."
    )
    coord._agent.verify = AsyncMock(return_value=output)

    result = await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    assert result.explanation == "Clear stitching defect."
    assert result.explanations == {"hi": "स्पष्ट सिलाई दोष।"}
    assert result.fault_party == "workshop"
    coord._verifications.save.assert_called_once_with(2, output, "photo.jpg", {"hi": "स्पष्ट सिलाई दोष।"})


@pytest.mark.anyio
async def test_translate_explanation_calls_agent_once_per_configured_language(monkeypatch):
    coord = _make_coordinator()
    monkeypatch.setattr(settings, "translation_target_languages", ["hi", "ta"])
    coord._agent.translate_explanation = AsyncMock(side_effect=["हिंदी अनुवाद", None])

    result = await coord._translate_explanation("Some defect note.")

    assert result == {"hi": "हिंदी अनुवाद"}
    assert coord._agent.translate_explanation.await_count == 2
    coord._agent.translate_explanation.assert_any_await("Some defect note.", "hi")
    coord._agent.translate_explanation.assert_any_await("Some defect note.", "ta")


@pytest.mark.anyio
async def test_translate_explanation_empty_when_no_target_languages(monkeypatch):
    coord = _make_coordinator()
    monkeypatch.setattr(settings, "translation_target_languages", [])

    result = await coord._translate_explanation("Some defect note.")

    assert result == {}
    coord._agent.translate_explanation.assert_not_called()


@pytest.mark.anyio
async def test_apply_verification_output_passes_translations_to_save():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=2, order_id=5))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.translate_explanation = AsyncMock(return_value="हिंदी अनुवाद")

    output = VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.95, explanation="Matches spec.",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    coord._verifications.save.assert_called_once_with(2, output, "photo.jpg", {"hi": "हिंदी अनुवाद"})


@pytest.mark.anyio
async def test_verification_trust_event_on_time_true_when_delivered_before_deadline():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(
        sublot_id=2, order_id=5, delivered_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    ))
    coord._orders.get = AsyncMock(return_value=_make_order_row(deadline=date(2026, 8, 1)))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(return_value=VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.95, explanation="Matches spec.",
    ))

    await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    trust_event = coord._trust.append_event.call_args.args[0]
    assert trust_event.on_time is True


@pytest.mark.anyio
async def test_verification_trust_event_on_time_false_when_delivered_after_deadline():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(
        sublot_id=2, order_id=5, delivered_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    ))
    coord._orders.get = AsyncMock(return_value=_make_order_row(deadline=date(2026, 8, 1)))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(return_value=VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.95, explanation="Matches spec.",
    ))

    await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    trust_event = coord._trust.append_event.call_args.args[0]
    assert trust_event.on_time is False


@pytest.mark.anyio
async def test_verification_trust_event_on_time_false_when_delivered_at_missing():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(
        sublot_id=2, order_id=5, delivered_at=None,
    ))
    coord._orders.get = AsyncMock(return_value=_make_order_row(deadline=date(2026, 8, 1)))
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(return_value=VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.95, explanation="Matches spec.",
    ))

    await coord.on_verification_complete(sublot_id=2, order_id=5, photo_path="photo.jpg")

    trust_event = coord._trust.append_event.call_args.args[0]
    assert trust_event.on_time is False


@pytest.mark.anyio
async def test_auto_verify_on_time_reflects_actual_delivery_timestamp():
    coord = _make_coordinator()
    coord._sublots.list_delivered_past_grace = AsyncMock(return_value=[1, 2])
    coord._sublots.get = AsyncMock(side_effect=[
        _make_sublot_row(
            sublot_id=1, order_id=10, workshop_id=7, status="DELIVERED",
            delivered_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        ),
        _make_sublot_row(
            sublot_id=2, order_id=11, workshop_id=8, status="DELIVERED",
            delivered_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        ),
    ])
    coord._orders.get = AsyncMock(return_value=_make_order_row(deadline=date(2026, 8, 1)))
    coord._trust.append_event = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.auto_verify_expired_deliveries()

    first_event, second_event = (
        call.args[0] for call in coord._trust.append_event.call_args_list
    )
    assert first_event.on_time is True
    assert second_event.on_time is False


@pytest.mark.anyio
async def test_on_sublot_delivered_sublot_not_found_returns_early():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=None)

    await coord.on_sublot_delivered(sublot_id=999, order_id=1, delivered_qty=10)

    coord._sublots.mark_delivered.assert_not_called()


@pytest.mark.anyio
async def test_on_verification_complete_sublot_not_found_returns_early():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=None)

    result = await coord.on_verification_complete(sublot_id=999, order_id=1, photo_path="p.jpg")

    coord._agent.verify.assert_not_called()
    assert result.status == "NOT_FOUND"
    assert result.explanation is None


@pytest.mark.anyio
async def test_needs_human_review_error_marks_needs_human_review():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row())
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(side_effect=NeedsHumanReviewError("loop exhausted"))

    result = await coord.on_verification_complete(sublot_id=3, order_id=7, photo_path="photo.jpg")

    coord._sublots.transition_status.assert_called_once_with(3, "NEEDS_HUMAN_REVIEW")
    assert result.status == "NEEDS_HUMAN_REVIEW"
    assert result.explanation is None


@pytest.mark.anyio
async def test_verification_error_marks_needs_human_review():
    from core.exceptions import VerificationError

    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row())
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(side_effect=VerificationError("API down"))

    result = await coord.on_verification_complete(
        sublot_id=3, order_id=7, photo_path="photo.jpg"
    )

    coord._sublots.transition_status.assert_called_once_with(3, "NEEDS_HUMAN_REVIEW")
    assert result.status == "NEEDS_HUMAN_REVIEW"
    assert result.explanation is None


@pytest.mark.anyio
async def test_retry_verification_resume_with_guidance_calls_agent_resume():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=7, order_id=4, status="NEEDS_HUMAN_REVIEW")
    )
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.is_resumable = AsyncMock(return_value=True)
    coord._agent.resume_with_guidance = AsyncMock(
        return_value=VerificationOutput(
            verdict="OK", fault_party="none", confidence=0.95, explanation="Looks fine."
        )
    )

    result = await coord.retry_verification(7, guidance="check the stitching again")

    coord._agent.resume_with_guidance.assert_awaited_once_with(7, "check the stitching again")
    coord._agent.resume_with_verdict.assert_not_called()
    coord._sublots.transition_status.assert_called_once_with(7, "VERIFIED")
    assert result.status == "VERIFIED"


@pytest.mark.anyio
async def test_retry_verification_resume_with_verdict_bypasses_the_model():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=8, order_id=4, status="NEEDS_HUMAN_REVIEW")
    )
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.is_resumable = AsyncMock(return_value=True)
    human_verdict = VerificationOutput(
        verdict="DEFECT", fault_party="workshop", confidence=1.0,
        explanation="Confirmed directly by an admin reviewer.",
    )
    coord._agent.resume_with_verdict = AsyncMock(return_value=human_verdict)

    result = await coord.retry_verification(8, verdict=human_verdict)

    coord._agent.resume_with_verdict.assert_awaited_once_with(8, human_verdict)
    coord._agent.resume_with_guidance.assert_not_called()
    coord._sublots.transition_status.assert_called_once_with(8, "FAILED")
    assert result.status == "FAILED"


@pytest.mark.anyio
async def test_retry_verification_resume_rejected_when_thread_not_resumable():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=9, order_id=4, status="NEEDS_HUMAN_REVIEW")
    )
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._agent.is_resumable = AsyncMock(return_value=False)

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        await coord.retry_verification(9, guidance="try again")

    assert "no paused verification" in str(exc_info.value).lower()
    coord._agent.resume_with_guidance.assert_not_called()


@pytest.mark.anyio
async def test_retry_verification_resume_still_paused_stays_needs_human_review():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=10, order_id=4, status="NEEDS_HUMAN_REVIEW")
    )
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.is_resumable = AsyncMock(return_value=True)
    coord._agent.resume_with_guidance = AsyncMock(
        side_effect=NeedsHumanReviewError("still stuck", thread_id="verification-sublot-10")
    )

    result = await coord.retry_verification(10, guidance="still unclear")

    coord._sublots.transition_status.assert_called_once_with(10, "NEEDS_HUMAN_REVIEW")
    assert result.status == "NEEDS_HUMAN_REVIEW"


@pytest.mark.anyio
async def test_retry_verification_plain_call_still_does_a_fresh_verify(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))
    photo_dir = tmp_path / "11"
    photo_dir.mkdir()
    (photo_dir / "defect.jpg").write_bytes(b"fake")

    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=11, order_id=4, status="NEEDS_HUMAN_REVIEW")
    )
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._verifications.save = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._agent.verify = AsyncMock(
        return_value=VerificationOutput(
            verdict="OK", fault_party="none", confidence=0.95, explanation="Looks fine."
        )
    )

    result = await coord.retry_verification(11)

    coord._agent.verify.assert_awaited_once()
    coord._agent.is_resumable.assert_not_called()
    assert result.status == "VERIFIED"


@pytest.mark.anyio
async def test_maybe_start_verifying_transitions_when_all_delivered():
    coord = _make_coordinator()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock()

    await coord._maybe_start_verifying(order_id=1)

    coord._orders.transition_status.assert_called_once_with(1, "IN_PRODUCTION", "VERIFYING")


@pytest.mark.anyio
async def test_maybe_start_verifying_noop_when_not_all_delivered():
    coord = _make_coordinator()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)

    await coord._maybe_start_verifying(order_id=1)

    coord._orders.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_maybe_start_verifying_swallows_invalid_transition():
    coord = _make_coordinator()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock(
        side_effect=InvalidStateTransitionError("already VERIFYING")
    )

    await coord._maybe_start_verifying(order_id=1)


@pytest.mark.anyio
async def test_on_sublot_delivered_releases_reserved_capacity():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=1, workshop_id=7, status="ASSIGNED", qty_assigned=100,
        )
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(product_type="kurta"))
    coord._orders.transition_status = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._workshops.get_factory = AsyncMock(return_value={"workshop_id": 99, "cost_per_unit": Decimal("180.00")})
    coord._sublots.create_batch = AsyncMock(return_value=[101])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=80)

    coord._sublots.mark_delivered.assert_any_call(1, 80)
    coord._workshops.release_capacity.assert_any_call(7, "kurta", 100)


@pytest.mark.anyio
async def test_on_sublot_delivered_partial_delivery_backfills_shortfall_as_pending():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=1, workshop_id=7, status="ASSIGNED", qty_assigned=100,
        )
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(product_type="kurta", status="IN_PRODUCTION"))
    coord._orders.transition_status = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._workshops.get_factory = AsyncMock(return_value={"workshop_id": 99, "cost_per_unit": Decimal("180.00")})
    coord._sublots.create_batch = AsyncMock(return_value=[101])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=80)

    coord._sublots.create_batch.assert_called_once_with([
        SubLotDraft(order_id=1, workshop_id=99, qty_assigned=20, cost_per_unit=Decimal("180.00"))
    ])
    coord._workshops.reserve_capacity.assert_called_once_with(99, "kurta", 20)
    coord._sublots.mark_delivered.assert_called_once_with(1, 80)
    coord._sublots.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_on_sublot_delivered_factory_shortfall_is_not_backfilled_to_itself():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=4, workshop_id=99, status="ASSIGNED", qty_assigned=100,
        )
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._orders.get = AsyncMock(
        return_value=_make_order_row(product_type="cotton-tote-bag", status="IN_PRODUCTION", factory_workshop_id=99)
    )
    coord._orders.transition_status = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._sublots.create_batch = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=4, order_id=2, delivered_qty=10)

    coord._sublots.create_batch.assert_not_called()
    coord._workshops.release_capacity.assert_called_once_with(99, "cotton-tote-bag", 100)


@pytest.mark.anyio
async def test_on_sublot_delivered_full_delivery_does_not_backfill():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=1, workshop_id=7, status="ASSIGNED", qty_assigned=100,
        )
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(product_type="kurta"))
    coord._workshops.release_capacity = AsyncMock()
    coord._sublots.create_batch = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=100)

    coord._sublots.create_batch.assert_not_called()

@pytest.mark.anyio
async def test_order_settles_only_after_factory_actually_delivers_the_backfill():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=1, workshop_id=7, status="ASSIGNED", qty_assigned=100,
        )
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(product_type="kurta", status="IN_PRODUCTION"))
    coord._orders.transition_status = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._workshops.get_factory = AsyncMock(return_value={"workshop_id": 99, "cost_per_unit": Decimal("180.00")})
    coord._sublots.create_batch = AsyncMock(return_value=[101])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=80)

    coord._orders.transition_status.assert_called_once_with(1, "IN_PRODUCTION", "FACTORY_FALLBACK")

    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(
            sublot_id=101, order_id=1, workshop_id=99, status="ASSIGNED", qty_assigned=20,
        )
    )
    coord._sublots.mark_capacity_released = AsyncMock(return_value=True)
    coord._sublots.all_terminal = AsyncMock(return_value=True)

    await coord.on_sublot_delivered(sublot_id=101, order_id=1, delivered_qty=20)

    coord._sublots.mark_delivered.assert_any_call(101, 20)
    coord._sublots.create_batch.assert_called_once()
    coord._orders.transition_status.assert_any_call(1, "IN_PRODUCTION", "SETTLING")


@pytest.mark.anyio
async def test_backfill_factory_shortfall_logs_and_skips_when_no_factory_configured():
    coord = _make_coordinator()
    coord._workshops.get_factory = AsyncMock(return_value=None)
    coord._sublots.create_batch = AsyncMock()

    await coord._backfill_factory_shortfall(order_id=1, product_type="kurta", shortfall_qty=20)

    coord._sublots.create_batch.assert_not_called()


@pytest.mark.anyio
async def test_backfill_factory_shortfall_swallows_factory_fallback_label_conflict():
    coord = _make_coordinator()
    coord._workshops.get_factory = AsyncMock(return_value={"workshop_id": 99, "cost_per_unit": Decimal("180.00")})
    coord._sublots.create_batch = AsyncMock(return_value=[101])
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.mark_delivered = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._orders.transition_status = AsyncMock(
        side_effect=InvalidStateTransitionError("already VERIFYING")
    )

    await coord._backfill_factory_shortfall(order_id=1, product_type="kurta", shortfall_qty=20)

    coord._workshops.reserve_capacity.assert_called_once_with(99, "kurta", 20)
    coord._sublots.mark_delivered.assert_not_called()
    coord._sublots.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_on_sublot_delivered_replay_does_not_double_release_capacity():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(
        return_value=_make_sublot_row(sublot_id=1, status="VERIFIED", qty_assigned=100)
    )
    coord._sublots.mark_delivered = AsyncMock()
    coord._sublots.mark_capacity_released = AsyncMock(return_value=False)
    coord._workshops.release_capacity = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(status="IN_PRODUCTION", product_type="kurta"))
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=False)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=80)

    coord._sublots.mark_delivered.assert_called_once_with(1, 80)
    coord._workshops.release_capacity.assert_not_called()


@pytest.mark.anyio
async def test_on_sublot_delivered_triggers_verifying_transition_when_all_delivered():
    coord = _make_coordinator()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(status="DELIVERED"))
    coord._sublots.mark_delivered = AsyncMock()
    coord._sublots.mark_capacity_released = AsyncMock(return_value=True)
    coord._workshops.release_capacity = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(status="IN_PRODUCTION", product_type="kurta"))
    coord._orders.transition_status = AsyncMock()
    coord._sublots.transition_status = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=True)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord.on_sublot_delivered(sublot_id=1, order_id=1, delivered_qty=50)

    coord._orders.transition_status.assert_any_call(1, "IN_PRODUCTION", "VERIFYING")

@pytest.mark.anyio
async def test_check_terminal_no_op_when_not_all_terminal():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    await coord._check_terminal_and_settle(order_id=1)

    coord._orders.transition_status.assert_not_called()
    coord._settlement.compute.assert_not_called()


@pytest.mark.anyio
async def test_check_terminal_in_production_path_closes_order():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock()
    coord._sublots.list_for_order = AsyncMock(return_value=[_make_sublot_row()])
    coord._verifications.get_for_order = AsyncMock(return_value={})

    result = MagicMock()
    result.buyer_total = Decimal("5250.00")
    result.platform_fee = Decimal("250.00")
    coord._settlement.compute = MagicMock(return_value=result)
    coord._payments.save_settlement = AsyncMock()

    await coord._check_terminal_and_settle(order_id=1)

    coord._payments.save_settlement.assert_called_once()
    coord._orders.transition_status.assert_called_with(1, "SETTLING", "CLOSED")


@pytest.mark.anyio
async def test_check_terminal_verifying_fallback():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)

    async def transition_side_effect(order_id, from_status, to_status):
        if from_status == "IN_PRODUCTION":
            raise InvalidStateTransitionError("order is in VERIFYING")

    coord._orders.transition_status = AsyncMock(side_effect=transition_side_effect)
    coord._sublots.list_for_order = AsyncMock(return_value=[_make_sublot_row()])
    coord._verifications.get_for_order = AsyncMock(return_value={})

    result = MagicMock()
    result.buyer_total = Decimal("0")
    result.platform_fee = Decimal("0")
    coord._settlement.compute = MagicMock(return_value=result)
    coord._payments.save_settlement = AsyncMock()

    await coord._check_terminal_and_settle(order_id=2)

    coord._payments.save_settlement.assert_called_once()


@pytest.mark.anyio
async def test_check_terminal_factory_fallback_path_closes_order():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)

    async def transition_side_effect(order_id, from_status, to_status):
        if from_status in ("IN_PRODUCTION", "VERIFYING"):
            raise InvalidStateTransitionError("order is in FACTORY_FALLBACK")

    coord._orders.transition_status = AsyncMock(side_effect=transition_side_effect)
    coord._sublots.list_for_order = AsyncMock(return_value=[_make_sublot_row()])
    coord._verifications.get_for_order = AsyncMock(return_value={})

    result = MagicMock()
    result.buyer_total = Decimal("0")
    result.platform_fee = Decimal("0")
    coord._settlement.compute = MagicMock(return_value=result)
    coord._payments.save_settlement = AsyncMock()

    await coord._check_terminal_and_settle(order_id=2)

    coord._payments.save_settlement.assert_called_once()


@pytest.mark.anyio
async def test_check_terminal_both_transitions_fail_skips_settlement():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock(
        side_effect=InvalidStateTransitionError("bad state")
    )

    await coord._check_terminal_and_settle(order_id=7)

    coord._settlement.compute.assert_not_called()
    coord._payments.save_settlement.assert_not_called()


@pytest.mark.anyio
async def test_create_advance_payment_noop_for_pay_on_delivery():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(payment_terms="PAY_ON_DELIVERY"))

    await coord.create_advance_payment(order_id=1)

    coord._buyer_payments.create_advance.assert_not_called()


@pytest.mark.anyio
async def test_create_advance_payment_noop_when_order_not_found():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=None)

    await coord.create_advance_payment(order_id=1)

    coord._buyer_payments.create_advance.assert_not_called()


@pytest.mark.anyio
async def test_create_advance_payment_full_amount_for_pay_upfront():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(
        payment_terms="PAY_UPFRONT", total_qty=100, factory_fallback_cost=Decimal("150.00"),
    ))

    await coord.create_advance_payment(order_id=1)

    # 100 * 150.00 = 15000.00, +5% platform fee = 15750.00, 100% upfront
    coord._buyer_payments.create_advance.assert_called_once_with(1, Decimal("15750.00"))


@pytest.mark.anyio
async def test_create_advance_payment_configured_percentage_for_advance_plus_balance():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(
        payment_terms="ADVANCE_PLUS_BALANCE", total_qty=100, factory_fallback_cost=Decimal("150.00"),
    ))

    await coord.create_advance_payment(order_id=1)

    # estimate 15750.00 * settings.settlement_advance_percentage (0.30) = 4725.00
    expected = (Decimal("15750.00") * settings.settlement_advance_percentage).quantize(Decimal("0.01"))
    coord._buyer_payments.create_advance.assert_called_once_with(1, expected)


@pytest.mark.anyio
async def test_create_cancellation_refund_noop_for_pay_on_delivery():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(payment_terms="PAY_ON_DELIVERY"))

    await coord.create_cancellation_refund(order_id=1)

    coord._buyer_payments.create_refund.assert_not_called()


@pytest.mark.anyio
async def test_create_cancellation_refund_noop_when_order_not_found():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=None)

    await coord.create_cancellation_refund(order_id=1)

    coord._buyer_payments.create_refund.assert_not_called()


@pytest.mark.anyio
async def test_create_cancellation_refund_noop_when_no_paid_advance():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(payment_terms="ADVANCE_PLUS_BALANCE"))
    coord._buyer_payments.get_for_order = AsyncMock(
        return_value=[{"kind": "ADVANCE", "amount": Decimal("3000.00"), "status": "PENDING"}]
    )

    await coord.create_cancellation_refund(order_id=1)

    coord._buyer_payments.create_refund.assert_not_called()


@pytest.mark.anyio
async def test_create_cancellation_refund_creates_refund_for_paid_advance():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(payment_terms="PAY_UPFRONT"))
    coord._buyer_payments.get_for_order = AsyncMock(
        return_value=[{"kind": "ADVANCE", "amount": Decimal("15750.00"), "status": "PAID"}]
    )

    await coord.create_cancellation_refund(order_id=1)

    coord._buyer_payments.create_refund.assert_called_once_with(1, Decimal("15750.00"))


@pytest.mark.anyio
async def test_settle_skips_buyer_payments_for_pay_on_delivery():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock()
    coord._orders.get = AsyncMock(return_value=_make_order_row(payment_terms="PAY_ON_DELIVERY"))
    coord._sublots.list_for_order = AsyncMock(return_value=[_make_sublot_row()])
    coord._verifications.get_for_order = AsyncMock(return_value={})

    result = MagicMock()
    result.buyer_total = Decimal("5250.00")
    result.platform_fee = Decimal("250.00")
    coord._settlement.compute = MagicMock(return_value=result)
    coord._payments.save_settlement = AsyncMock()

    await coord._check_terminal_and_settle(order_id=1)

    coord._buyer_payments.create_balance.assert_not_called()


@pytest.mark.anyio
async def test_settle_creates_balance_payment_for_advance_plus_balance():
    coord = _make_coordinator()
    coord._sublots.all_terminal = AsyncMock(return_value=True)
    coord._orders.transition_status = AsyncMock()
    coord._orders.get = AsyncMock(
        return_value=_make_order_row(payment_terms="ADVANCE_PLUS_BALANCE")
    )
    coord._sublots.list_for_order = AsyncMock(return_value=[_make_sublot_row()])
    coord._verifications.get_for_order = AsyncMock(return_value={})
    coord._buyer_payments.get_for_order = AsyncMock(
        return_value=[{"kind": "ADVANCE", "amount": Decimal("3000.00")}]
    )

    result = MagicMock()
    result.buyer_total = Decimal("10000.00")
    result.platform_fee = Decimal("500.00")
    coord._settlement.compute = MagicMock(return_value=result)
    coord._settlement.compute_balance_due = MagicMock(return_value=Decimal("7000.00"))
    coord._payments.save_settlement = AsyncMock()

    await coord._check_terminal_and_settle(order_id=1)

    coord._settlement.compute_balance_due.assert_called_once_with(
        Decimal("10000.00"), Decimal("3000.00")
    )
    coord._buyer_payments.create_balance.assert_called_once_with(1, Decimal("7000.00"))

@pytest.mark.anyio
async def test_defect_flagged_order_not_found_raises():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=None)

    with pytest.raises(InvalidStateTransitionError):
        await coord.on_defect_flagged(
            order_id=404, photo_path="p.jpg", defect_qty=5, description="torn"
        )

    coord._sublots.list_for_order.assert_not_called()


@pytest.mark.anyio
async def test_defect_flagged_no_delivered_sublot_raises():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=1, status="IN_PRODUCTION"),
        _make_sublot_row(sublot_id=2, status="ASSIGNED"),
    ])

    with pytest.raises(InvalidStateTransitionError):
        await coord.on_defect_flagged(
            order_id=1, photo_path="p.jpg", defect_qty=5, description="torn"
        )

    coord._sublots.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_defect_flagged_attributes_to_highest_id_delivered_sublot():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=1, status="DELIVERED"),
        _make_sublot_row(sublot_id=5, status="DELIVERED"),
        _make_sublot_row(sublot_id=9, status="ASSIGNED"),
    ])
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=5, status="VERIFYING"))
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._verifications.save = AsyncMock()
    coord._trust.append_event = AsyncMock()

    output = VerificationOutput(
        verdict="DEFECT", fault_party="workshop", confidence=0.9, explanation="torn seam",
    )
    coord._agent.verify = AsyncMock(return_value=output)

    await coord.on_defect_flagged(
        order_id=1, photo_path="p.jpg", defect_qty=5, description="torn seam"
    )

    coord._sublots.transition_status.assert_any_call(5, "VERIFYING")
    coord._agent.verify.assert_called_once()
    assert coord._agent.verify.call_args.args[3] == 5
    assert coord._agent.verify.call_args.kwargs["buyer_note"] == "5 units — torn seam"


@pytest.mark.anyio
async def test_defect_flagged_falls_back_to_verified_sublot_post_settlement():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row(status="CLOSED"))
    coord._orders.transition_status = AsyncMock(
        side_effect=InvalidStateTransitionError("already CLOSED")
    )
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=3, status="VERIFIED"),
        _make_sublot_row(sublot_id=7, status="VERIFIED"),
    ])
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=7, status="VERIFYING"))
    coord._sublots.all_terminal = AsyncMock(return_value=True)
    coord._verifications.save = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._agent.verify = AsyncMock(return_value=VerificationOutput(
        verdict="OK", fault_party="none", confidence=0.95, explanation="matches spec",
    ))

    result = await coord.on_defect_flagged(
        order_id=1, photo_path="p.jpg", defect_qty=2, description="discoloured"
    )

    coord._sublots.transition_status.assert_any_call(7, "VERIFYING")
    assert coord._agent.verify.call_args.args[3] == 7
    assert result.status == "VERIFIED"
    assert result.explanation == "matches spec"
    coord._payments.save_settlement.assert_not_called()


@pytest.mark.anyio
async def test_defect_flagged_returns_failed_status_for_confirmed_defect():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=_make_order_row())
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=5, status="DELIVERED"),
    ])
    coord._sublots.transition_status = AsyncMock()
    coord._sublots.get = AsyncMock(return_value=_make_sublot_row(sublot_id=5, status="VERIFYING"))
    coord._sublots.all_terminal = AsyncMock(return_value=False)
    coord._verifications.save = AsyncMock()
    coord._trust.append_event = AsyncMock()
    coord._agent.verify = AsyncMock(return_value=VerificationOutput(
        verdict="DEFECT", fault_party="workshop", confidence=0.9, explanation="torn seam",
    ))

    result = await coord.on_defect_flagged(
        order_id=1, photo_path="p.jpg", defect_qty=5, description="torn seam"
    )

    assert result.status == "FAILED"
    assert result.explanation == "torn seam"

@pytest.mark.anyio
async def test_enforce_deadline_order_not_found_raises():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value=None)

    with pytest.raises(InvalidStateTransitionError):
        await coord.enforce_deadline(order_id=404)


@pytest.mark.anyio
async def test_enforce_deadline_before_deadline_raises():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(
        return_value=_make_order_row(deadline=date(2099, 1, 1))
    )

    with pytest.raises(InvalidStateTransitionError):
        await coord.enforce_deadline(order_id=1)

    coord._sublots.list_for_order.assert_not_called()


@pytest.mark.anyio
async def test_enforce_deadline_marks_stuck_sublots_failed_and_releases_capacity():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(
        return_value=_make_order_row(deadline=date(2020, 1, 1), product_type="kurta")
    )
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=1, workshop_id=1, status="IN_PRODUCTION", qty_assigned=30),
        _make_sublot_row(sublot_id=2, workshop_id=2, status="ASSIGNED", qty_assigned=20),
        _make_sublot_row(sublot_id=3, workshop_id=3, status="VERIFIED", qty_assigned=50),
    ])
    coord._sublots.transition_status = AsyncMock()
    coord._workshops.release_capacity = AsyncMock()
    coord._workshops.get_factory = AsyncMock(return_value={"workshop_id": 99, "cost_per_unit": Decimal("180.00")})
    coord._sublots.create_batch = AsyncMock(side_effect=[[101], [102]])
    coord._sublots.mark_delivered = AsyncMock()
    coord._workshops.reserve_capacity = AsyncMock()
    coord._sublots.all_delivered_or_further = AsyncMock(return_value=True)
    coord._sublots.all_terminal = AsyncMock(return_value=False)

    failed_count = await coord.enforce_deadline(order_id=1)

    assert failed_count == 2
    coord._sublots.transition_status.assert_any_call(1, "FAILED")
    coord._sublots.transition_status.assert_any_call(2, "FAILED")
    coord._workshops.release_capacity.assert_any_call(1, "kurta", 30)
    coord._workshops.release_capacity.assert_any_call(2, "kurta", 20)
    assert coord._workshops.release_capacity.call_count == 2
    coord._sublots.create_batch.assert_any_call([
        SubLotDraft(order_id=1, workshop_id=99, qty_assigned=30, cost_per_unit=Decimal("180.00"))
    ])
    coord._sublots.create_batch.assert_any_call([
        SubLotDraft(order_id=1, workshop_id=99, qty_assigned=20, cost_per_unit=Decimal("180.00"))
    ])
    coord._workshops.reserve_capacity.assert_any_call(99, "kurta", 30)
    coord._workshops.reserve_capacity.assert_any_call(99, "kurta", 20)
    coord._sublots.mark_delivered.assert_not_called()


@pytest.mark.anyio
async def test_enforce_deadline_noop_when_nothing_stuck():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(
        return_value=_make_order_row(deadline=date(2020, 1, 1))
    )
    coord._sublots.list_for_order = AsyncMock(return_value=[
        _make_sublot_row(sublot_id=1, status="VERIFIED"),
    ])

    failed_count = await coord.enforce_deadline(order_id=1)

    assert failed_count == 0
    coord._sublots.transition_status.assert_not_called()
    coord._workshops.release_capacity.assert_not_called()

@pytest.mark.anyio
async def test_ensure_in_production_noop_when_already_in_production():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value={"status": "IN_PRODUCTION"})

    await coord._ensure_in_production(order_id=1)

    coord._orders.transition_status.assert_not_called()


@pytest.mark.anyio
async def test_ensure_in_production_concurrent_transition_does_not_raise():
    coord = _make_coordinator()
    coord._orders.get = AsyncMock(return_value={"status": "ALLOCATED"})
    coord._orders.transition_status = AsyncMock(
        side_effect=InvalidStateTransitionError("concurrent")
    )

    await coord._ensure_in_production(order_id=2)

def test_build_coordinator_wires_shared_repos_into_agent():
    fake_pool = MagicMock()

    coordinator = build_coordinator(fake_pool, MemorySaver())

    assert isinstance(coordinator, OrderCoordinator)
    assert coordinator._agent._orders is coordinator._orders
    assert coordinator._agent._trust is coordinator._trust
    assert coordinator._agent._verifications is coordinator._verifications
