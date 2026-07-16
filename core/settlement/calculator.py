from dataclasses import dataclass
from decimal import Decimal

from core.domain import (
    PaymentDraft,
    SettlementResult,
    SubLotRecord,
    VerificationRecord,
)


@dataclass(frozen=True)
class SettlementConfig:
    platform_fee_percentage: Decimal
    penalty_non_delivery_percentage: Decimal
    penalty_workshop_defect_percentage: Decimal


class SettlementCalculator:
    def __init__(self, config: SettlementConfig) -> None:
        self._config = config

    def compute(
        self,
        sublots: list[SubLotRecord],
        verifications: dict[int, VerificationRecord],
    ) -> SettlementResult:
        payments: list[PaymentDraft] = [
            self._compute_workshop_payment(sublot, verifications.get(sublot.sublot_id))
            for sublot in sublots
        ]

        buyer_base   = sum((p.base_amount for p in payments), Decimal("0"))
        platform_fee = (buyer_base * self._config.platform_fee_percentage).quantize(Decimal("0.01"))
        buyer_total  = buyer_base + platform_fee

        return SettlementResult(
            payments=payments,
            buyer_base=buyer_base,
            platform_fee=platform_fee,
            buyer_total=buyer_total,
        )

    def _compute_workshop_payment(
        self,
        sublot: SubLotRecord,
        verification: VerificationRecord | None,
    ) -> PaymentDraft:
        delivered = sublot.delivered_qty or 0
        base_amount = (Decimal(delivered) * sublot.cost_per_unit).quantize(Decimal("0.01"))
        penalty = self._compute_penalty(sublot, verification, base_amount)
        net_amount = (base_amount - penalty).quantize(Decimal("0.01"))

        return PaymentDraft(
            workshop_id=sublot.workshop_id,
            base_amount=base_amount,
            penalty=penalty,
            net_amount=net_amount,
        )

    def _compute_penalty(
        self,
        sublot: SubLotRecord,
        verification: VerificationRecord | None,
        base_amount: Decimal,
    ) -> Decimal:
        if sublot.status == "FAILED" and (sublot.delivered_qty or 0) == 0:
            assigned_value = (
                Decimal(sublot.qty_assigned) * sublot.cost_per_unit
            ).quantize(Decimal("0.01"))
            return (
                assigned_value * self._config.penalty_non_delivery_percentage
            ).quantize(Decimal("0.01"))

        if sublot.status == "NEEDS_HUMAN_REVIEW":
            return Decimal("0.00")

        if (
            verification is not None
            and verification.verdict == "DEFECT"
            and verification.fault_party == "workshop"
        ):
            return (
                base_amount * self._config.penalty_workshop_defect_percentage
            ).quantize(Decimal("0.01"))

        shortfall = sublot.qty_assigned - (sublot.delivered_qty or 0)
        if shortfall > 0:
            shortfall_value = (
                Decimal(shortfall) * sublot.cost_per_unit
            ).quantize(Decimal("0.01"))
            return (
                shortfall_value * self._config.penalty_non_delivery_percentage
            ).quantize(Decimal("0.01"))

        return Decimal("0.00")
