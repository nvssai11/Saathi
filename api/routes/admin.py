from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_coordinator, order_repo, require_admin, sublot_repo
from api.models import ReviewItem
from config import settings
from core.exceptions import InvalidStateTransitionError
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
        )
        for r in rows
    ]


@router.post("/sublots/{sublot_id}/retry-verification", status_code=status.HTTP_200_OK)
async def retry_sublot_verification(
    sublot_id: int,
    _: None = Depends(require_admin),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    try:
        result = await coordinator.retry_verification(sublot_id)
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RETRY_NOT_ELIGIBLE", "message": str(exc)},
        )

    return {"sublot_id": sublot_id, "status": result.status, "explanation": result.explanation}
