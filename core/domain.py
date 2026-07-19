from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class OrderSpec:
    order_id: int
    total_qty: int
    deadline: date
    quality_min: int
    allocation_date: date
    factory_fallback_cost: Decimal
    factory_workshop_id: int


@dataclass(frozen=True)
class WorkshopBid:
    workshop_id: int
    available_qty: int
    reserved_qty: int
    cost_per_unit: Decimal
    quality_tier: int
    lead_time_days: int
    trust_score: float
    spec_disputes: int

    @property
    def effective_qty(self) -> int:
        return self.available_qty - self.reserved_qty


@dataclass(frozen=True)
class SubLotDraft:
    order_id: int
    workshop_id: int
    qty_assigned: int
    cost_per_unit: Decimal


@dataclass(frozen=True)
class SublotAssignment:
    sublot_id: int
    order_id: int
    workshop_id: int
    product_type: str
    qty_assigned: int


@dataclass(frozen=True)
class TrustEvent:
    workshop_id: int
    sublot_id: int
    on_time: bool
    defect_found: bool
    fault_party: str
    created_at: datetime


@dataclass(frozen=True)
class VerificationOutput:
    verdict: str
    fault_party: str
    confidence: float
    explanation: str


@dataclass(frozen=True)
class VerificationCompletionResult:
    status: str
    explanation: str | None
    explanations: dict[str, str] = field(default_factory=dict)
    fault_party: str | None = None


@dataclass(frozen=True)
class SubLotRecord:
    sublot_id: int
    order_id: int
    workshop_id: int
    qty_assigned: int
    delivered_qty: int | None
    cost_per_unit: Decimal
    status: str
    delivered_at: datetime | None = None


@dataclass(frozen=True)
class VerificationRecord:
    sublot_id: int
    verdict: str
    fault_party: str
    confidence: float


@dataclass(frozen=True)
class PaymentDraft:
    workshop_id: int
    base_amount: Decimal
    penalty: Decimal
    net_amount: Decimal
    buyer_billable_amount: Decimal


@dataclass
class SettlementResult:
    payments: list[PaymentDraft]
    buyer_base: Decimal
    platform_fee: Decimal
    buyer_total: Decimal
