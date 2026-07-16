from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class PlaceOrderRequest(BaseModel):
    buyer_ref: str = Field(..., min_length=1, max_length=100)
    product_type: str = Field(..., min_length=1, max_length=100)
    total_qty: int = Field(..., gt=0)
    quality_min: int = Field(..., ge=1, le=5)
    deadline: date

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


def to_buyer_status_label(internal_status: str) -> str:
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

    @classmethod
    def from_db(
        cls,
        order_row,
        sublot_rows: list,
    ) -> "OrderStatusResponse":
        statuses = [s.status for s in sublot_rows]
        return cls(
            order_id=order_row["order_id"],
            correlation_id=str(order_row["correlation_id"]),
            status=to_buyer_status_label(order_row["status"]),
            total_qty=order_row["total_qty"],
            sublots_total=len(sublot_rows),
            sublots_delivered=sum(1 for s in statuses if s in ("DELIVERED", "VERIFIED")),
            sublots_verified=sum(1 for s in statuses if s == "VERIFIED"),
            sublots_failed=sum(1 for s in statuses if s == "FAILED"),
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


class TrustEventSummary(BaseModel):
    sublot_id: int
    on_time: bool
    defect_found: bool
    fault_party: str
    date: datetime


class TrustScoreResponse(BaseModel):
    workshop_id: int
    score: float
    grade: str
    explanation: list[str]
    history: list[TrustEventSummary]


class NotificationItem(BaseModel):
    notification_id: int
    order_id: int
    sublot_id: int
    product_type: str
    qty_assigned: int
    created_at: datetime


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
