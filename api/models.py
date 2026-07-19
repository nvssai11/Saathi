from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PlaceOrderRequest(BaseModel):
    buyer_ref: str = Field(..., min_length=1, max_length=100)
    product_type: str = Field(..., min_length=1, max_length=100)
    total_qty: int = Field(..., gt=0)
    quality_min: int = Field(..., ge=1, le=5)
    deadline: date
    payment_terms: Literal[
        "PAY_ON_DELIVERY", "PAY_UPFRONT", "ADVANCE_PLUS_BALANCE"
    ] = "PAY_ON_DELIVERY"

    @field_validator("deadline")
    @classmethod
    def deadline_must_be_future(cls, v: date) -> date:
        if v <= date.today():
            raise ValueError("deadline must be a future date")
        return v


class PlaceOrderResponse(BaseModel):
    order_id: int
    correlation_id: str
    status: str


_BUYER_STATUS_LABELS: dict[str, str] = {
    "PENDING":          "Received",
    "ALLOCATING":       "Processing",
    "ALLOCATED":        "Confirmed",
    "IN_PRODUCTION":    "In Production",
    "VERIFYING":        "Quality Check",
    "FACTORY_FALLBACK": "In Production",
    "SETTLING":         "Finalising",
    "CLOSED":           "Delivered",
    "FAILED":           "Failed",
    "CANCELLED":        "Cancelled",
}


def to_buyer_status_label(
    internal_status: str, sublots_verified: int = 0, sublots_failed: int = 0
) -> str:
    if internal_status == "CLOSED" and sublots_failed > 0:
        if sublots_verified == 0:
            return "Order failed quality check"
        return "Delivered — with quality issues"
    return _BUYER_STATUS_LABELS.get(internal_status, internal_status)


class OrderStatusResponse(BaseModel):
    order_id: int
    correlation_id: str
    status: str
    total_qty: int
    sublots_total: int
    sublots_delivered: int
    sublots_verified: int
    sublots_failed: int
    has_defect_photo: bool = False

    @classmethod
    def from_db(
        cls,
        order_row,
        sublot_rows: list,
        has_defect_photo: bool = False,
    ) -> "OrderStatusResponse":
        statuses = [s.status for s in sublot_rows]
        sublots_verified = sum(1 for s in statuses if s == "VERIFIED")
        sublots_failed = sum(1 for s in statuses if s == "FAILED")
        return cls(
            order_id=order_row["order_id"],
            correlation_id=str(order_row["correlation_id"]),
            status=to_buyer_status_label(order_row["status"], sublots_verified, sublots_failed),
            total_qty=order_row["total_qty"],
            sublots_total=len(sublot_rows),
            sublots_delivered=sum(1 for s in statuses if s in ("DELIVERED", "VERIFIED")),
            sublots_verified=sublots_verified,
            sublots_failed=sublots_failed,
            has_defect_photo=has_defect_photo,
        )


class OrderListItem(BaseModel):
    order_id: int
    status: str
    product_type: str
    total_qty: int
    deadline: date
    created_at: datetime


class OrderListResponse(BaseModel):
    orders: list[OrderListItem]
    total: int
    page: int
    page_size: int


class QuoteLineItem(BaseModel):
    product_type: str
    total_qty: int
    unit_price: Decimal
    subtotal: Decimal


class OrderQuoteResponse(BaseModel):
    order_id: int
    line_items: list[QuoteLineItem]
    platform_fee: Decimal
    total: Decimal


class SettlementSummaryResponse(BaseModel):
    order_id: int
    buyer_base: Decimal
    platform_fee: Decimal
    buyer_total: Decimal


class BuyerPaymentItem(BaseModel):
    buyer_payment_id: int
    kind: str
    amount: Decimal
    status: str
    created_at: datetime
    paid_at: datetime | None = None


class BuyerPaymentsResponse(BaseModel):
    order_id: int
    payment_terms: str
    items: list[BuyerPaymentItem]


class MarkDeliveredRequest(BaseModel):
    delivered_qty: int = Field(..., ge=0)


class WorkshopCapacityUpdateRequest(BaseModel):
    product_type: str = Field(..., min_length=1, max_length=100)
    available_qty: int = Field(..., ge=0)
    cost_per_unit: Decimal = Field(..., gt=0)
    lead_time_days: int = Field(..., gt=0)


class WorkshopCapacityResponse(BaseModel):
    workshop_id: int
    product_type: str
    available_qty: int
    cost_per_unit: Decimal
    lead_time_days: int
    updated_at: datetime


class WorkshopCapacityListItem(BaseModel):
    product_type: str
    available_qty: int
    in_transit_qty: int
    serving_capacity: int
    cost_per_unit: Decimal
    lead_time_days: int
    updated_at: datetime


class SubLotSummary(BaseModel):
    sublot_id: int
    order_id: int
    product_type: str
    deadline: date
    qty_assigned: int
    delivered_qty: int | None
    status: str
    is_factory: bool = False
    explanation: str | None = None
    explanations: dict[str, str] = Field(default_factory=dict)


class TrustEventSummary(BaseModel):
    sublot_id: int
    on_time: bool
    defect_found: bool
    fault_party: str
    date: datetime
    explanation: str | None = None
    explanations: dict[str, str] = Field(default_factory=dict)


class TrustScoreResponse(BaseModel):
    workshop_id: int
    score: float
    grade: str
    explanation: list[str]
    on_time_rate: float
    defect_rate: float
    window_count: int
    history: list[TrustEventSummary]


class NotificationItem(BaseModel):
    notification_id: int
    order_id: int
    sublot_id: int
    product_type: str
    qty_assigned: int
    created_at: datetime


class OtpRequestRequest(BaseModel):
    phone_number: str = Field(..., min_length=8, max_length=20)


class OtpRequestResponse(BaseModel):
    phone_number: str
    expires_in_seconds: int
    demo_code: str | None = None


class OtpVerifyRequest(BaseModel):
    phone_number: str = Field(..., min_length=8, max_length=20)
    code: str = Field(..., min_length=4, max_length=8)


class OtpVerifyResponse(BaseModel):
    token: str
    workshop_id: int
    workshop_name: str


class ReviewItem(BaseModel):
    sublot_id: int
    order_id: int
    workshop_id: int
    product_type: str
    qty_assigned: int
    status: str
    updated_at: datetime
    verdict: str | None = None
    fault_party: str | None = None
    confidence: float | None = None
    explanation: str | None = None
    explanations: dict[str, str] = Field(default_factory=dict)


class AllocationItem(BaseModel):
    sublot_id: int
    workshop_id: int
    workshop_name: str
    is_factory: bool
    qty_assigned: int
    delivered_qty: int | None = None
    cost_per_unit: Decimal
    status: str


class OrderAllocationResponse(BaseModel):
    order_id: int
    total_qty: int
    workshop_count: int
    sublots: list[AllocationItem]


class RetryVerificationRequest(BaseModel):
    guidance: str | None = Field(default=None, min_length=1, max_length=1000)
    verdict: str | None = Field(default=None, pattern="^(OK|DEFECT|SPEC_AMBIGUITY)$")
    fault_party: str | None = Field(default=None, pattern="^(workshop|buyer|none)$")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    explanation: str | None = Field(default=None, min_length=1, max_length=1000)
