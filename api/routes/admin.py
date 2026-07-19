from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_coordinator, notification_repo, order_repo, require_admin, sublot_repo
from api.models import AllocationItem, OrderAllocationResponse, ReviewItem, RetryVerificationRequest
from config import settings
from core.domain import VerificationOutput
from core.exceptions import InvalidStateTransitionError
from db.repositories.notification_repository import NotificationRepository
from db.repositories.order_repository import OrderRepository
from db.repositories.sublot_repository import SublotRepository
from events.producer import publish_order_placed
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/orders/{order_id}/enforce-deadline", status_code=status.HTTP_200_OK)
async def enforce_deadline(
    order_id: int,
    _: None = Depends(require_admin),
    orders: OrderRepository = Depends(order_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )

    try:
        failed_count = await coordinator.enforce_deadline(order_id)
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DEADLINE_NOT_PASSED", "message": str(exc)},
        )

    return {"order_id": order_id, "sublots_marked_failed": failed_count}


@router.post("/orders/reconcile-stuck", status_code=status.HTTP_200_OK)
async def reconcile_stuck_orders(
    _: None = Depends(require_admin),
    orders: OrderRepository = Depends(order_repo),
):
    stuck = await orders.list_stuck_pending(settings.stuck_order_threshold_seconds)
    for row in stuck:
        await publish_order_placed(row["order_id"], str(row["correlation_id"]))

    return {
        "republished_count": len(stuck),
        "order_ids": [row["order_id"] for row in stuck],
    }


@router.post("/orders/{order_id}/republish-notifications", status_code=status.HTTP_200_OK)
async def republish_notifications(
    order_id: int,
    _: None = Depends(require_admin),
    orders: OrderRepository = Depends(order_repo),
    sublots: SublotRepository = Depends(sublot_repo),
    notifications: NotificationRepository = Depends(notification_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )

    rows = await sublots.list_for_order_admin(order_id)
    for r in rows:
        await notifications.create(
            workshop_id=r["workshop_id"],
            order_id=order_id,
            sublot_id=r["sublot_id"],
            product_type=order_row["product_type"],
            qty_assigned=r["qty_assigned"],
        )

    return {"order_id": order_id, "notifications_republished": len(rows)}


@router.get("/orders/{order_id}/allocation", response_model=OrderAllocationResponse)
async def get_order_allocation(
    order_id: int,
    _: None = Depends(require_admin),
    orders: OrderRepository = Depends(order_repo),
    sublots: SublotRepository = Depends(sublot_repo),
):
    order_row = await orders.get(order_id)
    if order_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": f"Order {order_id} does not exist"},
        )

    rows = await sublots.list_for_order_admin(order_id)
    items = [
        AllocationItem(
            sublot_id=r["sublot_id"],
            workshop_id=r["workshop_id"],
            workshop_name=r["workshop_name"],
            is_factory=r["is_factory"],
            qty_assigned=r["qty_assigned"],
            delivered_qty=r["delivered_qty"],
            cost_per_unit=r["cost_per_unit"],
            status=r["status"],
        )
        for r in rows
    ]
    workshop_count = len({i.workshop_id for i in items if not i.is_factory})

    return OrderAllocationResponse(
        order_id=order_id,
        total_qty=order_row["total_qty"],
        workshop_count=workshop_count,
        sublots=items,
    )


@router.get("/sublots/needs-review", response_model=list[ReviewItem])
async def list_sublots_needing_review(
    _: None = Depends(require_admin),
    sublots: SublotRepository = Depends(sublot_repo),
):
    rows = await sublots.list_needing_review()
    return [
        ReviewItem(
            sublot_id=r["sublot_id"],
            order_id=r["order_id"],
            workshop_id=r["workshop_id"],
            product_type=r["product_type"],
            qty_assigned=r["qty_assigned"],
            status=r["status"],
            updated_at=r["updated_at"],
            verdict=r["verdict"],
            fault_party=r["fault_party"],
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            explanation=r["explanation"],
            explanations=r["explanations"] or {},
        )
        for r in rows
    ]


@router.post("/sublots/{sublot_id}/retry-verification", status_code=status.HTTP_200_OK)
async def retry_sublot_verification(
    sublot_id: int,
    body: RetryVerificationRequest | None = None,
    _: None = Depends(require_admin),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    body = body or RetryVerificationRequest()
    verdict = None
    if body.verdict is not None:
        verdict = VerificationOutput(
            verdict=body.verdict,
            fault_party=body.fault_party or "none",
            confidence=body.confidence if body.confidence is not None else 1.0,
            explanation=body.explanation or "Verdict entered directly by an admin reviewer.",
        )

    try:
        result = await coordinator.retry_verification(
            sublot_id, guidance=body.guidance, verdict=verdict,
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RETRY_NOT_ELIGIBLE", "message": str(exc)},
        )

    return {"sublot_id": sublot_id, "status": result.status, "explanation": result.explanation}
