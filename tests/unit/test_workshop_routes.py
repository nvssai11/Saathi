from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import asyncpg

from api.dependencies import (
    get_coordinator,
    notification_repo,
    require_workshop,
    sublot_repo,
    trust_repo,
    workshop_repo,
)
from api.errors import http_exception_handler
from api.routes.workshop import router as workshop_router
from config import settings
from core.domain import TrustEvent, VerificationCompletionResult
from core.trust.scorer import TrustScorer, TrustScorerConfig

app = FastAPI()
app.add_exception_handler(HTTPException, http_exception_handler)
app.include_router(workshop_router)
app.dependency_overrides[require_workshop] = lambda: 1

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    for dep in (trust_repo, sublot_repo, workshop_repo, notification_repo, get_coordinator):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides[require_workshop] = lambda: 1


def _make_sublot(sublot_id=1, order_id=10, workshop_id=1, status="ASSIGNED", qty_assigned=50, delivered_qty=None):
    row = MagicMock()
    row.sublot_id = sublot_id
    row.order_id = order_id
    row.workshop_id = workshop_id
    row.status = status
    row.qty_assigned = qty_assigned
    row.delivered_qty = delivered_qty
    return row


def _event(sublot_id: int, hour: int) -> TrustEvent:
    return TrustEvent(
        workshop_id=1,
        sublot_id=sublot_id,
        on_time=True,
        defect_found=False,
        fault_party="none",
        created_at=datetime(2026, 7, 16, hour, 0, tzinfo=timezone.utc),
    )


def test_trust_history_is_last_5_most_recent_first():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    trust = AsyncMock()
    trust.scorer = scorer
    trust.get_recent_events.return_value = [
        _event(sublot_id=i, hour=20 - i) for i in range(7)
    ]
    trust.get_recent_events_with_explanations.return_value = [
        {
            "sublot_id": i, "on_time": True, "defect_found": False, "fault_party": "none",
            "created_at": datetime(2026, 7, 16, 20 - i, 0, tzinfo=timezone.utc),
            "explanation": None, "explanations": {},
        }
        for i in range(5)
    ]
    app.dependency_overrides[trust_repo] = lambda: trust

    response = client.get("/workshop/trust")

    assert response.status_code == 200
    body = response.json()
    assert len(body["history"]) == 5
    assert [h["sublot_id"] for h in body["history"]] == [0, 1, 2, 3, 4]


def test_trust_response_includes_raw_rates_for_frontend_i18n():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    trust = AsyncMock()
    trust.scorer = scorer
    trust.get_recent_events.return_value = [
        _event(sublot_id=1, hour=10),
        _event(sublot_id=2, hour=9),
    ]
    trust.get_recent_events_with_explanations.return_value = []
    app.dependency_overrides[trust_repo] = lambda: trust

    response = client.get("/workshop/trust")

    assert response.status_code == 200
    body = response.json()
    assert body["window_count"] == 2
    assert body["on_time_rate"] == 1.0
    assert body["defect_rate"] == 0.0


def test_trust_history_empty_when_no_events():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    trust = AsyncMock()
    trust.scorer = scorer
    trust.get_recent_events.return_value = []
    app.dependency_overrides[trust_repo] = lambda: trust

    response = client.get("/workshop/trust")

    assert response.status_code == 200
    body = response.json()
    assert body["history"] == []
    assert body["score"] == 0.5

def test_list_my_sublots_includes_product_type_and_deadline():
    sublots = AsyncMock()
    sublots.list_for_workshop.return_value = [
        {
            "sublot_id": 1, "order_id": 10, "qty_assigned": 50, "delivered_qty": 30,
            "status": "IN_PRODUCTION", "product_type": "kurta", "deadline": date(2026, 9, 1),
            "explanation": None, "explanations": {},
        },
    ]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.get("/workshop/sublots")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["product_type"] == "kurta"
    assert body["deadline"] == "2026-09-01"
    sublots.list_for_workshop.assert_awaited_once_with(1, limit=settings.workshop_sublot_list_limit)

def test_list_my_sublots_surfaces_failure_explanation():
    sublots = AsyncMock()
    sublots.list_for_workshop.return_value = [
        {
            "sublot_id": 1, "order_id": 10, "qty_assigned": 50, "delivered_qty": 50,
            "status": "FAILED", "product_type": "kurta", "deadline": date(2026, 9, 1),
            "explanation": "Torn seam on left panel.",
            "explanations": {"hi": "बाएं पैनल पर सिलाई फटी हुई है।"},
        },
    ]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.get("/workshop/sublots")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["status"] == "FAILED"
    assert body["explanation"] == "Torn seam on left panel."
    assert body["explanations"]["hi"] == "बाएं पैनल पर सिलाई फटी हुई है।"


def test_trust_history_surfaces_defect_explanation():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    trust = AsyncMock()
    trust.scorer = scorer
    trust.get_recent_events.return_value = [_event(sublot_id=1, hour=10)]
    trust.get_recent_events_with_explanations.return_value = [
        {
            "sublot_id": 1, "on_time": True, "defect_found": True, "fault_party": "workshop",
            "created_at": datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc),
            "explanation": "Stitching defect on the right sleeve.",
            "explanations": {"hi": "दाहिनी आस्तीन पर सिलाई की खराबी।"},
        },
    ]
    app.dependency_overrides[trust_repo] = lambda: trust

    response = client.get("/workshop/trust")

    assert response.status_code == 200
    body = response.json()
    assert body["history"][0]["explanation"] == "Stitching defect on the right sleeve."
    assert body["history"][0]["explanations"]["hi"] == "दाहिनी आस्तीन पर सिलाई की खराबी।"


def test_update_capacity_200_upserts_and_returns_row():
    workshops = AsyncMock()
    workshops.upsert_capacity.return_value = {
        "workshop_id": 1, "product_type": "kurta", "available_qty": 150,
        "cost_per_unit": Decimal("95.00"), "lead_time_days": 14,
        "updated_at": datetime(2026, 7, 16, 12, 0),
    }
    app.dependency_overrides[workshop_repo] = lambda: workshops

    response = client.post(
        "/workshop/capacity",
        json={
            "product_type": "kurta", "available_qty": 150,
            "cost_per_unit": "95.00", "lead_time_days": 14,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workshop_id"] == 1
    assert body["available_qty"] == 150
    workshops.upsert_capacity.assert_awaited_once_with(
        workshop_id=1, product_type="kurta", available_qty=150,
        cost_per_unit=Decimal("95.00"), lead_time_days=14,
    )


def test_update_capacity_409_when_below_reserved():
    workshops = AsyncMock()
    workshops.upsert_capacity.side_effect = asyncpg.CheckViolationError("reserved_lte_available")
    app.dependency_overrides[workshop_repo] = lambda: workshops

    response = client.post(
        "/workshop/capacity",
        json={
            "product_type": "kurta", "available_qty": 5,
            "cost_per_unit": "95.00", "lead_time_days": 14,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CAPACITY_BELOW_RESERVED"

def test_mark_delivered_404_when_sublot_missing():
    sublots = AsyncMock()
    sublots.get.return_value = None
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.post("/workshop/sublots/1/deliver", json={"delivered_qty": 50})

    assert response.status_code == 404


def test_mark_delivered_403_when_wrong_workshop():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=2, status="ASSIGNED")
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.post("/workshop/sublots/1/deliver", json={"delivered_qty": 50})

    assert response.status_code == 403


def test_mark_delivered_409_when_already_past_deliverable_state():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=1, status="VERIFIED")
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.post("/workshop/sublots/1/deliver", json={"delivered_qty": 50})

    assert response.status_code == 409


def test_mark_delivered_409_when_delivered_qty_exceeds_assigned():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=1, status="ASSIGNED", qty_assigned=50)
    sublots.mark_delivered = AsyncMock()
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.post("/workshop/sublots/1/deliver", json={"delivered_qty": 51})

    assert response.status_code == 409
    sublots.mark_delivered.assert_not_awaited()


def test_mark_delivered_202_happy_path_writes_db_then_publishes_kafka():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(sublot_id=1, order_id=10, workshop_id=1, status="ASSIGNED")
    sublots.mark_delivered = AsyncMock()
    app.dependency_overrides[sublot_repo] = lambda: sublots

    with patch("api.routes.workshop.publish_sublot_delivered", new=AsyncMock()) as mock_publish:
        response = client.post("/workshop/sublots/1/deliver", json={"delivered_qty": 40})

    assert response.status_code == 202
    assert response.json() == {"sublot_id": 1, "delivered_qty": 40}
    sublots.mark_delivered.assert_awaited_once_with(1, 40)
    mock_publish.assert_awaited_once_with(1, 10, 40)

def test_start_production_404_when_sublot_missing():
    sublots = AsyncMock()
    sublots.get.return_value = None
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post("/workshop/sublots/1/start-production")

    assert response.status_code == 404


def test_start_production_403_when_wrong_workshop():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=2, status="ASSIGNED")
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post("/workshop/sublots/1/start-production")

    assert response.status_code == 403


def test_start_production_409_when_not_assigned():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=1, status="IN_PRODUCTION")
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post("/workshop/sublots/1/start-production")

    assert response.status_code == 409


def test_start_production_202_happy_path_calls_coordinator():
    assigned_sublot = _make_sublot(sublot_id=1, workshop_id=1, status="ASSIGNED")
    in_production_sublot = _make_sublot(sublot_id=1, workshop_id=1, status="IN_PRODUCTION")
    sublots = AsyncMock()
    sublots.get.side_effect = [assigned_sublot, in_production_sublot]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    coordinator = AsyncMock()
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post("/workshop/sublots/1/start-production")

    assert response.status_code == 202
    assert response.json() == {"sublot_id": 1, "status": "IN_PRODUCTION"}
    coordinator.on_production_started.assert_awaited_once_with(1)

def test_list_capacity_200_returns_available_in_transit_serving_capacity():
    workshops = AsyncMock()
    workshops.list_capacity.return_value = [
        {
            "product_type": "kurta", "available_qty": 300, "reserved_qty": 250,
            "cost_per_unit": Decimal("95.00"), "lead_time_days": 14,
            "updated_at": datetime(2026, 7, 17, 9, 0),
        },
    ]
    app.dependency_overrides[workshop_repo] = lambda: workshops

    response = client.get("/workshop/capacity")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["product_type"] == "kurta"
    assert body["available_qty"] == 300
    assert body["in_transit_qty"] == 250
    assert body["serving_capacity"] == 50
    workshops.list_capacity.assert_awaited_once_with(1)


def test_list_capacity_empty_when_no_products():
    workshops = AsyncMock()
    workshops.list_capacity.return_value = []
    app.dependency_overrides[workshop_repo] = lambda: workshops

    response = client.get("/workshop/capacity")

    assert response.status_code == 200
    assert response.json() == []

def test_upload_photo_404_when_sublot_missing():
    sublots = AsyncMock()
    sublots.get.return_value = None
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post(
        "/workshop/sublots/1/photo",
        files={"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
    )

    assert response.status_code == 404


def test_upload_photo_403_when_wrong_workshop():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=2, status="DELIVERED")
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post(
        "/workshop/sublots/1/photo",
        files={"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
    )

    assert response.status_code == 403


def test_upload_photo_409_when_not_delivered_yet():
    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=1, status="IN_PRODUCTION")
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post(
        "/workshop/sublots/1/photo",
        files={"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
    )

    assert response.status_code == 409


def test_upload_photo_202_happy_path_writes_file_and_calls_coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))

    delivered_sublot = _make_sublot(sublot_id=1, order_id=10, workshop_id=1, status="DELIVERED")
    sublots = AsyncMock()
    sublots.get.return_value = delivered_sublot
    sublots.transition_status = AsyncMock()
    app.dependency_overrides[sublot_repo] = lambda: sublots

    coordinator = AsyncMock()
    coordinator.on_verification_complete.return_value = VerificationCompletionResult(
        status="VERIFIED", explanation="Matches spec.",
    )
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post(
        "/workshop/sublots/1/photo",
        files={"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
    )

    assert response.status_code == 202
    assert response.json() == {"sublot_id": 1, "status": "VERIFIED", "explanation": "Matches spec."}
    sublots.transition_status.assert_awaited_once_with(1, "VERIFYING")
    coordinator.on_verification_complete.assert_awaited_once()
    call_kwargs = coordinator.on_verification_complete.call_args.kwargs
    assert call_kwargs["sublot_id"] == 1
    assert call_kwargs["order_id"] == 10
    written_files = list(tmp_path.rglob("defect*"))
    assert len(written_files) == 1
    assert written_files[0].read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"


def test_upload_photo_no_explanation_when_verdict_never_produced(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))

    sublots = AsyncMock()
    sublots.get.return_value = _make_sublot(workshop_id=1, status="DELIVERED")
    sublots.transition_status = AsyncMock()
    app.dependency_overrides[sublot_repo] = lambda: sublots

    coordinator = AsyncMock()
    coordinator.on_verification_complete.return_value = VerificationCompletionResult(
        status="NEEDS_HUMAN_REVIEW", explanation=None,
    )
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post(
        "/workshop/sublots/1/photo",
        files={"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")},
    )

    assert response.status_code == 202
    assert response.json() == {"sublot_id": 1, "status": "NEEDS_HUMAN_REVIEW", "explanation": None}

def test_list_notifications_200_returns_own_notifications():
    notifications = AsyncMock()
    notifications.list_for_workshop.return_value = [
        {
            "notification_id": 2, "order_id": 42, "sublot_id": 3,
            "product_type": "kurta", "qty_assigned": 60,
            "created_at": datetime(2026, 7, 17, 9, 0),
        },
        {
            "notification_id": 1, "order_id": 41, "sublot_id": 2,
            "product_type": "kurta", "qty_assigned": 40,
            "created_at": datetime(2026, 7, 16, 9, 0),
        },
    ]
    app.dependency_overrides[notification_repo] = lambda: notifications

    response = client.get("/workshop/notifications")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["sublot_id"] == 3
    assert body[0]["qty_assigned"] == 60
    notifications.list_for_workshop.assert_awaited_once_with(1, limit=settings.workshop_notification_list_limit)


def test_list_notifications_empty_when_none():
    notifications = AsyncMock()
    notifications.list_for_workshop.return_value = []
    app.dependency_overrides[notification_repo] = lambda: notifications

    response = client.get("/workshop/notifications")

    assert response.status_code == 200
    assert response.json() == []
