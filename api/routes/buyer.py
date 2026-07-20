from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.dependencies import (
    buyer_payment_repo,
    get_coordinator,
    order_repo,
    payment_repo,
    sublot_repo,
    require_buyer,
    verification_repo,
    workshop_repo,
)
from api.uploads import save_defect_photo
from api.models import (
    BuyerPaymentItem,
    BuyerPaymentsResponse,
    OrderListItem,
    OrderListResponse,
    OrderQuoteResponse,
    OrderStatusResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
    QuoteLineItem,
    SettlementSummaryResponse,
    to_buyer_status_label,
)
from config import settings
from core.exceptions import InvalidStateTransitionError
from core.media_types import SUPPORTED_IMAGE_EXTENSIONS
from db.repositories.buyer_payment_repository import BuyerPaymentRepository
from db.repositories.order_repository import OrderRepository
from db.repositories.payment_repository import PaymentRepository
from db.repositories.sublot_repository import SublotRepository
from db.repositories.verification_repository import VerificationRepository
from db.repositories.workshop_repository import WorkshopRepository
from events.producer import publish_order_placed
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["buyer"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PlaceOrderResponse)
async def place_order(
    body: PlaceOrderRequest,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    workshops: WorkshopRepository = Depends(workshop_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    factory = await workshops.get_factory(body.product_type)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No factory configured for product_type '{body.product_type}'",
        )

    order_id = await orders.create(
        buyer_ref=body.buyer_ref,
        product_type=body.product_type,
        total_qty=body.total_qty,
        quality_min=body.quality_min,
        deadline=body.deadline,
        factory_fallback_cost=Decimal(str(factory["cost_per_unit"])),
        factory_workshop_id=factory["workshop_id"],
        payment_terms=body.payment_terms,
    )
    await coordinator.create_advance_payment(order_id)

    order_row = await orders.get(order_id)
    correlation_id = str(order_row["correlation_id"])
    response.headers["X-Correlation-ID"] = correlation_id

    await publish_order_placed(order_id, correlation_id)

    return PlaceOrderResponse(
        order_id=order_id,
        correlation_id=correlation_id,
        status="Received",
    )


@router.get("", response_model=OrderListResponse)
async def list_orders(
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
):
    rows, total = await orders.list_paginated(status_filter, page, page_size)
    return OrderListResponse(
        orders=[
            OrderListItem(
                order_id=r["order_id"],
                status=to_buyer_status_label(r["status"]),
                product_type=r["product_type"],
                total_qty=r["total_qty"],
                deadline=r["deadline"],
                created_at=r["created_at"],
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{order_id}", response_model=OrderStatusResponse)
async def get_order_status(
    order_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    sublots: SublotRepository = Depends(sublot_repo),
    verifications: VerificationRepository = Depends(verification_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    sublot_rows = await sublots.list_for_order(order_id)
    photo_path = await verifications.get_latest_photo_path_for_order(order_id)
    return OrderStatusResponse.from_db(order_row, sublot_rows, has_defect_photo=photo_path is not None)


@router.get("/{order_id}/defect-photo")
async def get_order_defect_photo(
    order_id: int,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    verifications: VerificationRepository = Depends(verification_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    photo_path = await verifications.get_latest_photo_path_for_order(order_id)
    if photo_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No defect photo on file for this order",
        )

    path = Path(photo_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Defect photo is on record but missing from disk",
        )

    media_type = SUPPORTED_IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@router.get("/{order_id}/quote", response_model=OrderQuoteResponse)
async def get_order_quote(
    order_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    sublots: SublotRepository = Depends(sublot_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    sublot_rows = await sublots.list_for_order(order_id)

    if not sublot_rows:
        unit_price = Decimal(str(order_row["factory_fallback_cost"]))
        subtotal = unit_price * order_row["total_qty"]
        line_items = [QuoteLineItem(
            product_type=order_row["product_type"],
            total_qty=order_row["total_qty"],
            unit_price=unit_price,
            subtotal=subtotal,
        )]
    else:
        total_qty = sum(s.qty_assigned for s in sublot_rows)
        total_cost = sum(
            Decimal(s.qty_assigned) * s.cost_per_unit for s in sublot_rows
        )
        avg_unit = (total_cost / total_qty).quantize(Decimal("0.01")) if total_qty else Decimal("0")
        line_items = [QuoteLineItem(
            product_type=order_row["product_type"],
            total_qty=total_qty,
            unit_price=avg_unit,
            subtotal=total_cost.quantize(Decimal("0.01")),
        )]

    subtotal = sum(item.subtotal for item in line_items)
    fee = (subtotal * settings.platform_fee_percentage).quantize(Decimal("0.01"))
    return OrderQuoteResponse(
        order_id=order_id,
        line_items=line_items,
        platform_fee=fee,
        total=subtotal + fee,
    )


@router.get("/{order_id}/invoice", response_model=SettlementSummaryResponse)
async def get_order_invoice(
    order_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    payments: PaymentRepository = Depends(payment_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])
    if order_row["status"] != "CLOSED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ORDER_NOT_SETTLED",
                "message": f"Order {order_id} has not been settled yet (status: {order_row['status']})",
            },
        )

    payment_rows = await payments.get_for_order(order_id)
    buyer_base = sum(
        (Decimal(str(p["buyer_billable_amount"])) for p in payment_rows), Decimal("0")
    )
    platform_fee = (buyer_base * settings.platform_fee_percentage).quantize(Decimal("0.01"))
    buyer_total = buyer_base + platform_fee

    return SettlementSummaryResponse(
        order_id=order_id,
        buyer_base=buyer_base,
        platform_fee=platform_fee,
        buyer_total=buyer_total,
    )


@router.get("/{order_id}/payments", response_model=BuyerPaymentsResponse)
async def get_order_payments(
    order_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    buyer_payments: BuyerPaymentRepository = Depends(buyer_payment_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    rows = await buyer_payments.get_for_order(order_id)
    return BuyerPaymentsResponse(
        order_id=order_id,
        payment_terms=order_row["payment_terms"],
        items=[_to_buyer_payment_item(row) for row in rows],
    )


@router.post("/{order_id}/payments/{payment_id}/pay", response_model=BuyerPaymentItem)
async def pay_order_payment(
    order_id: int,
    payment_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    buyer_payments: BuyerPaymentRepository = Depends(buyer_payment_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    updated = await buyer_payments.mark_paid(order_id, payment_id)
    if updated is None:
        existing = await buyer_payments.get_for_order(order_id)
        match = next((r for r in existing if r["buyer_payment_id"] == payment_id), None)
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PAYMENT_NOT_FOUND", "message": f"Payment {payment_id} not found for order {order_id}"},
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_PAID", "message": f"Payment {payment_id} is already paid"},
        )

    return _to_buyer_payment_item(updated)


def _to_buyer_payment_item(row) -> BuyerPaymentItem:
    return BuyerPaymentItem(
        buyer_payment_id=row["buyer_payment_id"],
        kind=row["kind"],
        amount=row["amount"],
        status=row["status"],
        created_at=row["created_at"],
        paid_at=row["paid_at"],
    )


@router.post("/{order_id}/flag-defect", status_code=status.HTTP_202_ACCEPTED)
async def flag_order_defect(
    order_id: int,
    response: Response,
    photo: UploadFile = File(...),
    defect_qty: int = Form(..., gt=0),
    description: str = Form(..., min_length=1, max_length=1000),
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    upload_dir = Path(settings.upload_directory) / "order-defects" / str(order_id)
    photo_path = await save_defect_photo(photo, upload_dir)

    try:
        result = await coordinator.on_defect_flagged(
            order_id=order_id,
            photo_path=photo_path,
            defect_qty=defect_qty,
            description=description,
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NO_DELIVERED_SUBLOT", "message": str(exc)},
        )

    return {
        "order_id": order_id,
        "defect_qty": defect_qty,
        "verification_status": result.status,
        "explanation": result.explanation,
        "explanations": result.explanations,
        "fault_party": result.fault_party,
    }


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_order(
    order_id: int,
    response: Response,
    _: None = Depends(require_buyer),
    orders: OrderRepository = Depends(order_repo),
    sublots: SublotRepository = Depends(sublot_repo),
    workshops: WorkshopRepository = Depends(workshop_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    response.headers["X-Correlation-ID"] = str(order_row["correlation_id"])

    try:
        await orders.cancel(order_id)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    sublot_rows = await sublots.list_for_order(order_id)
    for s in sublot_rows:
        await workshops.release_capacity(
            s.workshop_id, order_row["product_type"], s.qty_assigned
        )
    await sublots.cancel_for_order(order_id)
    await coordinator.create_cancellation_refund(order_id)
