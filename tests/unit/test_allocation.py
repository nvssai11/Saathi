from unittest.mock import patch

import pulp
import pytest
from datetime import date
from decimal import Decimal

from core.allocation.engine import AllocationEngine, AllocationConfig
from core.domain import OrderSpec, WorkshopBid, SubLotDraft

@pytest.fixture
def config() -> AllocationConfig:
    return AllocationConfig(
        trust_minimum_threshold=0.30,
        trust_penalty_factor=0.5,
        spec_disputes_threshold=3,
        spec_disputes_mip_penalty_factor=0.10,
    )


@pytest.fixture
def engine(config: AllocationConfig) -> AllocationEngine:
    return AllocationEngine(config)


def _order(total_qty: int = 100, quality_min: int = 2) -> OrderSpec:
    return OrderSpec(
        order_id=1,
        total_qty=total_qty,
        deadline=date(2026, 8, 1),
        quality_min=quality_min,
        allocation_date=date(2026, 7, 16),
        factory_fallback_cost=Decimal("200.00"),
        factory_workshop_id=99,
    )


def _bid(
    workshop_id: int = 1,
    available_qty: int = 100,
    reserved_qty: int = 0,
    cost_per_unit: Decimal = Decimal("100.00"),
    quality_tier: int = 3,
    lead_time_days: int = 10,
    trust_score: float = 0.80,
    spec_disputes: int = 0,
) -> WorkshopBid:
    return WorkshopBid(
        workshop_id=workshop_id,
        available_qty=available_qty,
        reserved_qty=reserved_qty,
        cost_per_unit=cost_per_unit,
        quality_tier=quality_tier,
        lead_time_days=lead_time_days,
        trust_score=trust_score,
        spec_disputes=spec_disputes,
    )

def test_demand_fully_satisfied(engine: AllocationEngine):
    bids = [_bid(workshop_id=i, available_qty=60) for i in range(1, 3)]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assert sum(d.qty_assigned for d in drafts) == 100


def test_single_workshop_gets_full_order(engine: AllocationEngine):
    drafts = engine.allocate(_order(total_qty=50), [_bid(available_qty=50)])
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 1
    assert drafts[0].qty_assigned == 50


def test_capacity_constraint_respected(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=40, reserved_qty=0),
        _bid(workshop_id=2, available_qty=80, reserved_qty=0),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(1, 0) <= 40
    assert assigned.get(2, 0) <= 80


def test_reserved_qty_reduces_effective_capacity(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=50, reserved_qty=30),
        _bid(workshop_id=2, available_qty=100, reserved_qty=0),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(1, 0) <= 20


def test_factory_fallback_when_no_eligible_bids(engine: AllocationEngine):
    bids = [_bid(workshop_id=i, trust_score=0.20) for i in range(1, 4)]
    drafts = engine.allocate(_order(total_qty=60), bids)
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 60
    assert drafts[0].cost_per_unit == Decimal("200.00")


def test_factory_fallback_when_quality_too_low(engine: AllocationEngine):
    bids = [_bid(quality_tier=1)]
    drafts = engine.allocate(_order(quality_min=2), bids)
    assert all(d.workshop_id == 99 for d in drafts)


def test_factory_fallback_when_deadline_infeasible(engine: AllocationEngine):
    order = _order(total_qty=50)
    deadline_days = (order.deadline - order.allocation_date).days
    bids = [_bid(lead_time_days=deadline_days + 1)]
    drafts = engine.allocate(order, bids)
    assert all(d.workshop_id == 99 for d in drafts)


def test_cheaper_workshop_preferred(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=100, cost_per_unit=Decimal("80.00")),
        _bid(workshop_id=2, available_qty=100, cost_per_unit=Decimal("150.00")),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(1, 0) == 100
    assert assigned.get(2, 0) == 0


def test_trust_penalty_makes_cheap_but_low_trust_less_attractive(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=100, cost_per_unit=Decimal("80.00"), trust_score=0.30),
        _bid(workshop_id=2, available_qty=100, cost_per_unit=Decimal("100.00"), trust_score=1.0),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(2, 0) == 100


def test_spec_disputes_adds_mip_penalty(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=100, cost_per_unit=Decimal("100.00"),
             trust_score=0.80, spec_disputes=3),
        _bid(workshop_id=2, available_qty=100, cost_per_unit=Decimal("100.00"),
             trust_score=0.80, spec_disputes=0),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(2, 0) == 100


def test_insufficient_workshop_capacity_routes_full_order_to_factory(engine: AllocationEngine):
    bids = [_bid(workshop_id=1, available_qty=60)]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 100


def test_exactly_sufficient_workshop_capacity_is_not_routed_to_factory(engine: AllocationEngine):
    bids = [_bid(workshop_id=1, available_qty=100)]
    drafts = engine.allocate(_order(total_qty=100), bids)
    factory_drafts = [d for d in drafts if d.workshop_id == 99]
    assert not factory_drafts
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(1, 0) == 100


def test_more_than_sufficient_workshop_capacity_uses_normal_split(engine: AllocationEngine):
    bids = [
        _bid(workshop_id=1, available_qty=80, cost_per_unit=Decimal("90.00")),
        _bid(workshop_id=2, available_qty=80, cost_per_unit=Decimal("95.00")),
    ]
    drafts = engine.allocate(_order(total_qty=100), bids)
    factory_drafts = [d for d in drafts if d.workshop_id == 99]
    assert not factory_drafts
    assert sum(d.qty_assigned for d in drafts) == 100


def test_order_id_propagated(engine: AllocationEngine):
    drafts = engine.allocate(_order(), [_bid()])
    assert all(d.order_id == 1 for d in drafts)


def test_cost_per_unit_snapshot_is_bid_cost(engine: AllocationEngine):
    cost = Decimal("123.45")
    drafts = engine.allocate(_order(total_qty=50), [_bid(cost_per_unit=cost)])
    workshop_drafts = [d for d in drafts if d.workshop_id != 99]
    assert all(d.cost_per_unit == cost for d in workshop_drafts)


def test_empty_bids_falls_back_to_factory(engine: AllocationEngine):
    drafts = engine.allocate(_order(total_qty=50), bids=[])
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 50


def test_factory_bid_in_bids_is_stripped_before_mip(engine: AllocationEngine):
    order = _order(total_qty=100)
    factory_bid = _bid(
        workshop_id=order.factory_workshop_id,
        available_qty=9999,
        cost_per_unit=order.factory_fallback_cost,
        trust_score=0.99,
    )
    real_bid = _bid(workshop_id=1, available_qty=100, cost_per_unit=Decimal("80.00"))

    drafts = engine.allocate(order, bids=[factory_bid, real_bid])

    assert sum(d.qty_assigned for d in drafts) == 100
    factory_drafts = [d for d in drafts if d.workshop_id == 99]
    assert len(factory_drafts) <= 1


def test_exclusion_reasons_collects_all_failing_constraints(engine: AllocationEngine):
    order = _order(total_qty=50, quality_min=3)
    deadline_days = (order.deadline - order.allocation_date).days
    bad_bid = _bid(
        trust_score=0.10,
        quality_tier=1,
        lead_time_days=deadline_days + 5,
    )
    reasons = engine._exclusion_reasons(bad_bid, order.quality_min, deadline_days)
    assert len(reasons) == 3
    assert any("trust_score" in r for r in reasons)
    assert any("quality_tier" in r for r in reasons)
    assert any("lead_time" in r for r in reasons)


def test_correct_rounding_drift_over_assigned_all_factory(engine: AllocationEngine):
    from core.domain import SubLotDraft
    order = _order(total_qty=100)
    drafts = [
        SubLotDraft(
            order_id=order.order_id,
            workshop_id=order.factory_workshop_id,
            qty_assigned=101,
            cost_per_unit=order.factory_fallback_cost,
        )
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 100
    assert drafts[0].workshop_id == order.factory_workshop_id
    assert drafts[0].qty_assigned == 100


def test_correct_rounding_drift_under_assignment_creates_factory():
    from core.domain import SubLotDraft
    config = AllocationConfig(
        trust_minimum_threshold=0.30,
        trust_penalty_factor=0.5,
        spec_disputes_threshold=3,
        spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = _order(total_qty=100)
    drafts = [
        SubLotDraft(
            order_id=order.order_id,
            workshop_id=1,
            qty_assigned=99,
            cost_per_unit=Decimal("100.00"),
        )
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 100
    factory_drafts = [d for d in drafts if d.workshop_id == order.factory_workshop_id]
    assert len(factory_drafts) == 1
    assert factory_drafts[0].qty_assigned == 1


def test_correct_rounding_drift_pop_removes_one_unit_sublot():
    from core.domain import SubLotDraft
    config = AllocationConfig(
        trust_minimum_threshold=0.30,
        trust_penalty_factor=0.5,
        spec_disputes_threshold=3,
        spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = _order(total_qty=10)
    drafts = [
        SubLotDraft(order_id=order.order_id, workshop_id=1, qty_assigned=1,
                    cost_per_unit=Decimal("100.00")),
        SubLotDraft(order_id=order.order_id, workshop_id=order.factory_workshop_id,
                    qty_assigned=10, cost_per_unit=order.factory_fallback_cost),
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 10
    assert not any(d.workshop_id == 1 for d in drafts)


def test_correct_rounding_drift_over_assigned_spans_multiple_drafts():
    config = AllocationConfig(
        trust_minimum_threshold=0.30, trust_penalty_factor=0.5,
        spec_disputes_threshold=3, spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = _order(total_qty=1)
    drafts = [
        SubLotDraft(order_id=order.order_id, workshop_id=1, qty_assigned=2, cost_per_unit=Decimal("100.00")),
        SubLotDraft(order_id=order.order_id, workshop_id=2, qty_assigned=2, cost_per_unit=Decimal("90.00")),
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 1


def test_correct_rounding_drift_over_assigned_prefers_non_factory_across_multiple_drafts():
    config = AllocationConfig(
        trust_minimum_threshold=0.30, trust_penalty_factor=0.5,
        spec_disputes_threshold=3, spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = _order(total_qty=1)
    drafts = [
        SubLotDraft(order_id=order.order_id, workshop_id=1, qty_assigned=2, cost_per_unit=Decimal("100.00")),
        SubLotDraft(order_id=order.order_id, workshop_id=order.factory_workshop_id, qty_assigned=2, cost_per_unit=order.factory_fallback_cost),
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 1
    assert not any(d.workshop_id == 1 for d in drafts)
    factory_drafts = [d for d in drafts if d.workshop_id == order.factory_workshop_id]
    assert len(factory_drafts) == 1
    assert factory_drafts[0].qty_assigned == 1


def test_insufficient_capacity_check_uses_effective_not_gross_available_qty(engine: AllocationEngine):
    bids = [_bid(workshop_id=1, available_qty=100, reserved_qty=90)]
    drafts = engine.allocate(_order(total_qty=100), bids)
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 100


def test_effective_qty_exclusion_reason_reported(engine: AllocationEngine):
    order = _order(total_qty=50)
    deadline_days = (order.deadline - order.allocation_date).days
    bid = _bid(available_qty=50, reserved_qty=50)
    reasons = engine._exclusion_reasons(bid, order.quality_min, deadline_days)
    assert len(reasons) == 1
    assert "effective_qty=0" in reasons[0]


def test_all_solvers_failing_routes_to_factory(engine: AllocationEngine):
    with patch.object(pulp.LpProblem, "solve", side_effect=pulp.PulpSolverError("no solver binary")):
        drafts = engine.allocate(_order(total_qty=75), [_bid(available_qty=100)])
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 75


def test_non_optimal_solver_status_routes_to_factory(engine: AllocationEngine):
    with patch.object(pulp.LpProblem, "solve", return_value=None):
        drafts = engine.allocate(_order(total_qty=40), [_bid(available_qty=100)])
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 40


def test_mip_voluntarily_uses_factory_alongside_a_workshop_when_cheaper(engine: AllocationEngine):
    order = _order(total_qty=100)
    order = OrderSpec(
        order_id=order.order_id, total_qty=100, deadline=order.deadline,
        quality_min=order.quality_min, allocation_date=order.allocation_date,
        factory_fallback_cost=Decimal("80.00"), factory_workshop_id=99,
    )
    bids = [
        _bid(workshop_id=1, available_qty=30, cost_per_unit=Decimal("50.00")),
        _bid(workshop_id=2, available_qty=100, cost_per_unit=Decimal("200.00")),
    ]
    drafts = engine.allocate(order, bids)
    assigned = {d.workshop_id: d.qty_assigned for d in drafts}
    assert assigned.get(1, 0) == 30
    assert assigned.get(99, 0) == 70
    assert assigned.get(2, 0) == 0
    assert sum(d.qty_assigned for d in drafts) == 100


def test_correct_rounding_drift_under_assignment_grows_existing_factory_draft():
    config = AllocationConfig(
        trust_minimum_threshold=0.30, trust_penalty_factor=0.5,
        spec_disputes_threshold=3, spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = _order(total_qty=100)
    drafts = [
        SubLotDraft(order_id=order.order_id, workshop_id=1, qty_assigned=60, cost_per_unit=Decimal("100.00")),
        SubLotDraft(order_id=order.order_id, workshop_id=order.factory_workshop_id, qty_assigned=39, cost_per_unit=order.factory_fallback_cost),
    ]
    engine._correct_rounding_drift(order, drafts)
    assert sum(d.qty_assigned for d in drafts) == 100
    factory_drafts = [d for d in drafts if d.workshop_id == order.factory_workshop_id]
    assert len(factory_drafts) == 1
    assert factory_drafts[0].qty_assigned == 40


def test_order_already_overdue_at_allocation_routes_to_factory(engine: AllocationEngine):
    order = OrderSpec(
        order_id=1, total_qty=50, deadline=date(2026, 7, 1), quality_min=2,
        allocation_date=date(2026, 7, 16), factory_fallback_cost=Decimal("200.00"),
        factory_workshop_id=99,
    )
    bid = _bid(workshop_id=1, available_qty=100, lead_time_days=1)
    drafts = engine.allocate(order, [bid])
    assert len(drafts) == 1
    assert drafts[0].workshop_id == 99
    assert drafts[0].qty_assigned == 50


def test_allocation_error_when_no_factory_configured():
    from core.exceptions import AllocationError
    config = AllocationConfig(
        trust_minimum_threshold=0.30,
        trust_penalty_factor=0.5,
        spec_disputes_threshold=3,
        spec_disputes_mip_penalty_factor=0.10,
    )
    engine = AllocationEngine(config)
    order = OrderSpec(
        order_id=42,
        total_qty=100,
        deadline=date(2026, 8, 1),
        quality_min=2,
        allocation_date=date(2026, 7, 16),
        factory_fallback_cost=Decimal("200.00"),
        factory_workshop_id=0,
    )
    bids = [_bid(trust_score=0.10)]
    with pytest.raises(AllocationError):
        engine.allocate(order, bids)
