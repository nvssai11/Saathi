import pytest
from decimal import Decimal

from core.settlement.calculator import SettlementCalculator, SettlementConfig
from core.domain import SubLotRecord, VerificationRecord

@pytest.fixture
def config() -> SettlementConfig:
    return SettlementConfig(
        platform_fee_percentage=Decimal("0.05"),
        penalty_non_delivery_percentage=Decimal("0.20"),
    )


@pytest.fixture
def calculator(config: SettlementConfig) -> SettlementCalculator:
    return SettlementCalculator(config)


def _sublot(
    sublot_id: int = 1,
    order_id: int = 1,
    workshop_id: int = 1,
    qty_assigned: int = 100,
    delivered_qty: int | None = 100,
    cost_per_unit: Decimal = Decimal("100.00"),
    status: str = "VERIFIED",
) -> SubLotRecord:
    return SubLotRecord(
        sublot_id=sublot_id,
        order_id=order_id,
        workshop_id=workshop_id,
        qty_assigned=qty_assigned,
        delivered_qty=delivered_qty,
        cost_per_unit=cost_per_unit,
        status=status,
    )


def _verification(
    sublot_id: int = 1,
    verdict: str = "OK",
    fault_party: str = "none",
    confidence: float = 0.95,
) -> VerificationRecord:
    return VerificationRecord(
        sublot_id=sublot_id,
        verdict=verdict,
        fault_party=fault_party,
        confidence=confidence,
    )

def test_buyer_billed_only_for_delivered_units(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=80, cost_per_unit=Decimal("100.00"))
    result = calculator.compute([sublot], {})
    assert result.buyer_base == Decimal("8000.00")


def test_buyer_not_billed_for_failed_sublot(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=0, status="FAILED")
    result = calculator.compute([sublot], {})
    assert result.buyer_base == Decimal("0.00")


def test_platform_fee_is_5_percent_of_buyer_base(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("200.00"))
    result = calculator.compute([sublot], {})
    assert result.buyer_base == Decimal("20000.00")
    assert result.platform_fee == Decimal("1000.00")
    assert result.buyer_total == Decimal("21000.00")

def test_non_delivery_penalty_is_20pct_of_assigned_value(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=0, status="FAILED",
                     cost_per_unit=Decimal("100.00"))
    result = calculator.compute([sublot], {})
    payment = result.payments[0]
    assert payment.base_amount == Decimal("0.00")
    assert payment.penalty == Decimal("2000.00")
    assert payment.net_amount == Decimal("-2000.00")

def test_workshop_defect_penalty_is_full_writeoff_of_delivered_value(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("100.00"),
                     status="VERIFIED")
    verification = _verification(verdict="DEFECT", fault_party="workshop")
    result = calculator.compute([sublot], {1: verification})
    payment = result.payments[0]
    assert payment.base_amount == Decimal("10000.00")
    assert payment.penalty == Decimal("10000.00")
    assert payment.net_amount == Decimal("0.00")


def test_buyer_not_billed_for_confirmed_workshop_defect(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("100.00"))
    verification = _verification(verdict="DEFECT", fault_party="workshop")
    result = calculator.compute([sublot], {1: verification})
    assert result.buyer_base == Decimal("0.00")
    assert result.platform_fee == Decimal("0.00")
    assert result.buyer_total == Decimal("0.00")


def test_buyer_fault_defect_still_billed_normally(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("100.00"))
    verification = _verification(verdict="DEFECT", fault_party="buyer")
    result = calculator.compute([sublot], {1: verification})
    assert result.buyer_base == Decimal("10000.00")


def test_multi_sublot_buyer_base_excludes_only_the_confirmed_defect_sublot(
    calculator: SettlementCalculator,
):
    sublots = [
        _sublot(sublot_id=1, workshop_id=1, qty_assigned=50, delivered_qty=50,
                cost_per_unit=Decimal("100.00")),
        _sublot(sublot_id=2, workshop_id=2, qty_assigned=50, delivered_qty=50,
                cost_per_unit=Decimal("100.00")),
    ]
    verifications = {2: _verification(sublot_id=2, verdict="DEFECT", fault_party="workshop")}
    result = calculator.compute(sublots, verifications)

    assert result.buyer_base == Decimal("5000.00")
    payments = {p.workshop_id: p for p in result.payments}
    assert payments[1].net_amount == Decimal("5000.00")
    assert payments[2].net_amount == Decimal("0.00")


def test_buyer_fault_defect_no_penalty(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("100.00"))
    verification = _verification(verdict="DEFECT", fault_party="buyer")
    result = calculator.compute([sublot], {1: verification})
    payment = result.payments[0]
    assert payment.penalty == Decimal("0.00")
    assert payment.net_amount == Decimal("10000.00")


def test_spec_ambiguity_no_penalty(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100)
    verification = _verification(verdict="SPEC_AMBIGUITY", fault_party="buyer")
    result = calculator.compute([sublot], {1: verification})
    assert result.payments[0].penalty == Decimal("0.00")

def test_no_verification_record_means_no_penalty(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=50, delivered_qty=50, cost_per_unit=Decimal("100.00"))
    result = calculator.compute([sublot], {})
    payment = result.payments[0]
    assert payment.penalty == Decimal("0.00")
    assert payment.net_amount == Decimal("5000.00")

def test_multi_sublot_buyer_base_sums_delivered_only(calculator: SettlementCalculator):
    sublots = [
        _sublot(sublot_id=1, workshop_id=1, qty_assigned=50, delivered_qty=50,
                cost_per_unit=Decimal("100.00"), status="VERIFIED"),
        _sublot(sublot_id=2, workshop_id=2, qty_assigned=50, delivered_qty=0,
                status="FAILED"),
    ]
    result = calculator.compute(sublots, {})
    assert result.buyer_base == Decimal("5000.00")
    assert result.platform_fee == Decimal("250.00")
    assert result.buyer_total == Decimal("5250.00")


def test_multi_sublot_per_workshop_payments_correct(calculator: SettlementCalculator):
    sublots = [
        _sublot(sublot_id=1, workshop_id=1, qty_assigned=60, delivered_qty=60,
                cost_per_unit=Decimal("100.00")),
        _sublot(sublot_id=2, workshop_id=2, qty_assigned=40, delivered_qty=40,
                cost_per_unit=Decimal("120.00")),
    ]
    verifications = {
        1: _verification(sublot_id=1, verdict="OK"),
        2: _verification(sublot_id=2, verdict="DEFECT", fault_party="workshop"),
    }
    result = calculator.compute(sublots, verifications)
    payments = {p.workshop_id: p for p in result.payments}

    assert payments[1].net_amount == Decimal("6000.00")
    assert payments[2].penalty == Decimal("4800.00")
    assert payments[2].net_amount == Decimal("0.00")

def test_amounts_have_two_decimal_places(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=3, delivered_qty=3, cost_per_unit=Decimal("33.33"))
    result = calculator.compute([sublot], {})
    assert result.buyer_base == Decimal("99.99")
    assert result.platform_fee == Decimal("5.00")

def test_needs_human_review_sublot_gets_full_payment_no_penalty(calculator: SettlementCalculator):
    sublot = _sublot(
        qty_assigned=100,
        delivered_qty=100,
        cost_per_unit=Decimal("100.00"),
        status="NEEDS_HUMAN_REVIEW",
    )
    result = calculator.compute([sublot], {})
    payment = result.payments[0]
    assert payment.penalty == Decimal("0.00")
    assert payment.net_amount == Decimal("10000.00")
    assert result.buyer_base == Decimal("10000.00")


def test_needs_human_review_with_a_defect_record_still_gets_no_penalty(
    calculator: SettlementCalculator,
):
    sublot = _sublot(
        qty_assigned=100,
        delivered_qty=100,
        cost_per_unit=Decimal("100.00"),
        status="NEEDS_HUMAN_REVIEW",
    )
    verification = _verification(verdict="DEFECT", fault_party="workshop", confidence=0.80)
    result = calculator.compute([sublot], {1: verification})
    payment = result.payments[0]
    assert payment.penalty == Decimal("0.00")
    assert payment.net_amount == Decimal("10000.00")


def test_failed_with_partial_delivery_gets_shortfall_penalty(calculator: SettlementCalculator):
    sublot = _sublot(
        qty_assigned=100,
        delivered_qty=30,
        cost_per_unit=Decimal("100.00"),
        status="FAILED",
    )
    result = calculator.compute([sublot], {})
    payment = result.payments[0]
    assert payment.base_amount == Decimal("3000.00")
    assert payment.penalty == Decimal("1400.00")
    assert payment.net_amount == Decimal("1600.00")

def test_partial_delivery_shortfall_penalty_on_undelivered_portion_only(calculator: SettlementCalculator):
    sublot = _sublot(
        qty_assigned=100, delivered_qty=80, cost_per_unit=Decimal("100.00"), status="VERIFIED",
    )
    result = calculator.compute([sublot], {})
    payment = result.payments[0]
    assert payment.base_amount == Decimal("8000.00")
    assert payment.penalty == Decimal("400.00")
    assert payment.net_amount == Decimal("7600.00")


def test_partial_delivery_with_workshop_defect_uses_defect_penalty_not_shortfall():
    config = SettlementConfig(
        platform_fee_percentage=Decimal("0.05"),
        penalty_non_delivery_percentage=Decimal("0.20"),
    )
    calculator = SettlementCalculator(config)
    sublot = _sublot(
        qty_assigned=100, delivered_qty=80, cost_per_unit=Decimal("100.00"), status="VERIFIED",
    )
    verification = _verification(verdict="DEFECT", fault_party="workshop")
    result = calculator.compute([sublot], {1: verification})
    payment = result.payments[0]
    assert payment.base_amount == Decimal("8000.00")
    assert payment.penalty == Decimal("8000.00")
    assert payment.net_amount == Decimal("0.00")


def test_full_delivery_has_no_shortfall_penalty(calculator: SettlementCalculator):
    sublot = _sublot(qty_assigned=100, delivered_qty=100, cost_per_unit=Decimal("100.00"))
    result = calculator.compute([sublot], {})
    assert result.payments[0].penalty == Decimal("0.00")
