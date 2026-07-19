from __future__ import annotations

import logging
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from api.dependencies import (
    get_coordinator,
    notification_repo,
    require_workshop,
    sublot_repo,
    trust_repo,
    workshop_repo,
)
from api.uploads import save_defect_photo
from api.models import (
    MarkDeliveredRequest,
    NotificationItem,
    SubLotSummary,
    TrustEventSummary,
    TrustScoreResponse,
    WorkshopCapacityListItem,
    WorkshopCapacityResponse,
    WorkshopCapacityUpdateRequest,
)
from config import settings
from db.repositories.notification_repository import NotificationRepository
from db.repositories.sublot_repository import SublotRepository
from db.repositories.trust_repository import TrustRepository
from db.repositories.workshop_repository import WorkshopRepository
from events.producer import publish_sublot_delivered
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workshop", tags=["workshop"])


@router.post("/capacity", response_model=WorkshopCapacityResponse)
async def update_my_capacity(
    body: WorkshopCapacityUpdateRequest,
    workshop_id: int = Depends(require_workshop),
    workshops: WorkshopRepository = Depends(workshop_repo),
):
    try:
        row = await workshops.upsert_capacity(
            workshop_id=workshop_id,
            product_type=body.product_type,
            available_qty=body.available_qty,
            cost_per_unit=body.cost_per_unit,
            lead_time_days=body.lead_time_days,
        )
    except asyncpg.CheckViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CAPACITY_BELOW_RESERVED",
                "message": "available_qty cannot be set below currently reserved capacity",
            },
        )

    return WorkshopCapacityResponse(
        workshop_id=row["workshop_id"],
        product_type=row["product_type"],
        available_qty=row["available_qty"],
        cost_per_unit=row["cost_per_unit"],
        lead_time_days=row["lead_time_days"],
        updated_at=row["updated_at"],
    )


@router.get("/capacity", response_model=list[WorkshopCapacityListItem])
async def list_my_capacity(
    workshop_id: int = Depends(require_workshop),
    workshops: WorkshopRepository = Depends(workshop_repo),
):
    rows = await workshops.list_capacity(workshop_id)
    return [
        WorkshopCapacityListItem(
            product_type=r["product_type"],
            available_qty=r["available_qty"],
            in_transit_qty=r["reserved_qty"],
            serving_capacity=r["available_qty"] - r["reserved_qty"],
            cost_per_unit=r["cost_per_unit"],
            lead_time_days=r["lead_time_days"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.get("/sublots", response_model=list[SubLotSummary])
async def list_my_sublots(
    workshop_id: int = Depends(require_workshop),
    sublots: SublotRepository = Depends(sublot_repo),
):
    records = await sublots.list_for_workshop(workshop_id, limit=settings.workshop_sublot_list_limit)
    return [
        SubLotSummary(
            sublot_id=r["sublot_id"],
            order_id=r["order_id"],
            product_type=r["product_type"],
            deadline=r["deadline"],
            qty_assigned=r["qty_assigned"],
            delivered_qty=r["delivered_qty"],
            status=r["status"],
            explanation=r["explanation"],
            explanations=r["explanations"] or {},
        )
        for r in records
    ]


@router.post("/sublots/{sublot_id}/deliver", status_code=status.HTTP_202_ACCEPTED)
async def mark_sublot_delivered(
    sublot_id: int,
    body: MarkDeliveredRequest,
    workshop_id: int = Depends(require_workshop),
    sublots: SublotRepository = Depends(sublot_repo),
):
    sublot = await sublots.get(sublot_id)
    if sublot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sublot not found")
    if sublot.workshop_id != workshop_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if sublot.status not in ("ASSIGNED", "IN_PRODUCTION"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sublot is in state {sublot.status}, cannot mark delivered",
        )
    if body.delivered_qty > sublot.qty_assigned:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"delivered_qty ({body.delivered_qty}) cannot exceed "
                f"qty_assigned ({sublot.qty_assigned})"
            ),
        )

    await sublots.mark_delivered(sublot_id, body.delivered_qty)
    await publish_sublot_delivered(sublot_id, sublot.order_id, body.delivered_qty)

    return {"sublot_id": sublot_id, "delivered_qty": body.delivered_qty}


@router.post("/sublots/{sublot_id}/start-production", status_code=status.HTTP_202_ACCEPTED)
async def start_sublot_production(
    sublot_id: int,
    workshop_id: int = Depends(require_workshop),
    sublots: SublotRepository = Depends(sublot_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    sublot = await sublots.get(sublot_id)
    if sublot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sublot not found")
    if sublot.workshop_id != workshop_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if sublot.status != "ASSIGNED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sublot is in state {sublot.status}, cannot start production",
        )

    await coordinator.on_production_started(sublot_id)

    sublot_updated = await sublots.get(sublot_id)
    return {"sublot_id": sublot_id, "status": sublot_updated.status if sublot_updated else "UNKNOWN"}


@router.post("/sublots/{sublot_id}/photo", status_code=status.HTTP_202_ACCEPTED)
async def upload_defect_photo(
    sublot_id: int,
    photo: UploadFile = File(...),
    workshop_id: int = Depends(require_workshop),
    sublots: SublotRepository = Depends(sublot_repo),
    coordinator: OrderCoordinator = Depends(get_coordinator),
):
    sublot = await sublots.get(sublot_id)
    if sublot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sublot not found")
    if sublot.workshop_id != workshop_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if sublot.status != "DELIVERED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sublot must be in DELIVERED state to submit a photo (current: {sublot.status})",
        )

    upload_dir = Path(settings.upload_directory) / str(sublot_id)
    photo_path = await save_defect_photo(photo, upload_dir)

    await sublots.transition_status(sublot_id, "VERIFYING")

    result = await coordinator.on_verification_complete(
        sublot_id=sublot_id,
        order_id=sublot.order_id,
        photo_path=photo_path,
    )

    return {"sublot_id": sublot_id, "status": result.status, "explanation": result.explanation}


@router.get("/trust", response_model=TrustScoreResponse)
async def get_my_trust_score(
    workshop_id: int = Depends(require_workshop),
    trust: TrustRepository = Depends(trust_repo),
):
    events = await trust.get_recent_events(workshop_id, limit=settings.trust_window_size)
    score = trust.scorer.compute_score(events)
    grade = trust.scorer.grade(score)
    explanation = trust.scorer.score_explanation(events)
    on_time_rate = trust.scorer.compute_on_time_rate(events)
    defect_rate = trust.scorer.compute_defect_rate(events)
    window_count = trust.scorer.window_count(events)

    recent_with_explanations = await trust.get_recent_events_with_explanations(workshop_id, limit=5)
    history = [
        TrustEventSummary(
            sublot_id=r["sublot_id"],
            on_time=r["on_time"],
            defect_found=r["defect_found"],
            fault_party=r["fault_party"],
            date=r["created_at"],
            explanation=r["explanation"],
            explanations=r["explanations"] or {},
        )
        for r in recent_with_explanations
    ]

    return TrustScoreResponse(
        workshop_id=workshop_id,
        score=score,
        grade=grade,
        explanation=explanation,
        on_time_rate=on_time_rate,
        defect_rate=defect_rate,
        window_count=window_count,
        history=history,
    )


@router.get("/notifications", response_model=list[NotificationItem])
async def list_my_notifications(
    workshop_id: int = Depends(require_workshop),
    notifications: NotificationRepository = Depends(notification_repo),
):
    rows = await notifications.list_for_workshop(workshop_id, limit=settings.workshop_notification_list_limit)
    return [
        NotificationItem(
            notification_id=r["notification_id"],
            order_id=r["order_id"],
            sublot_id=r["sublot_id"],
            product_type=r["product_type"],
            qty_assigned=r["qty_assigned"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
