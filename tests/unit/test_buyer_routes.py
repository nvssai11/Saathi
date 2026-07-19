from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.dependencies import (
    get_coordinator,
    order_repo,
    payment_repo,
    require_buyer,
    sublot_repo,
    verification_repo,
    workshop_repo,
)
from api.errors import http_exception_handler
from api.routes.buyer import router as buyer_router
from config import settings
from core.domain import VerificationCompletionResult
from core.exceptions import InvalidStateTransitionError

app = FastAPI()
app.add_exception_handler(HTTPException, http_exception_handler)
app.include_router(buyer_router)
app.dependency_overrides[require_buyer] = lambda: None

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    for dep in (order_repo, payment_repo, get_coordinator, sublot_repo, verification_repo, workshop_repo):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides[require_buyer] = lambda: None


def _mock_orders(order_row):
    orders = AsyncMock()
    orders.get.return_value = order_row
    return orders


def _mock_verifications(photo_path=None):
    verifications = AsyncMock()
    verifications.get_latest_photo_path_for_order.return_value = photo_path
    return verifications


def _mock_payments(rows=()):
    payments = AsyncMock()
    payments.get_for_order.return_value = list(rows)
    return payments


def _mock_sublot(qty_assigned: int, cost_per_unit: Decimal) -> MagicMock:
    s = MagicMock()
    s.qty_assigned = qty_assigned
    s.cost_per_unit = cost_per_unit
    return s

def test_place_order_422_when_no_factory_for_product_type():
    workshops = AsyncMock()
    workshops.get_factory.return_value = None
    app.dependency_overrides[workshop_repo] = lambda: workshops
    app.dependency_overrides[order_repo] = lambda: AsyncMock()

    response = client.post(
        "/orders",
        json={
            "buyer_ref": "buyer-1", "product_type": "unknown-type", "total_qty": 10,
            "quality_min": 2, "deadline": "2099-01-01",
        },
    )

    assert response.status_code == 422


def test_place_order_201_happy_path_creates_publishes_and_returns_correlation_id():
    workshops = AsyncMock()
    workshops.get_factory.return_value = {"workshop_id": 99, "cost_per_unit": Decimal("180.00")}
    app.dependency_overrides[workshop_repo] = lambda: workshops

    orders = AsyncMock()
    orders.create.return_value = 42
    orders.get.return_value = {"correlation_id": "corr-xyz"}
    app.dependency_overrides[order_repo] = lambda: orders

    with patch("api.routes.buyer.publish_order_placed", new=AsyncMock()) as mock_publish:
        response = client.post(
            "/orders",
            json={
                "buyer_ref": "buyer-1", "product_type": "kurta", "total_qty": 100,
                "quality_min": 2, "deadline": "2099-01-01",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body == {"order_id": 42, "correlation_id": "corr-xyz", "status": "Received"}
    assert response.headers["X-Correlation-ID"] == "corr-xyz"
    orders.create.assert_awaited_once_with(
        buyer_ref="buyer-1", product_type="kurta", total_qty=100, quality_min=2,
        deadline=date(2099, 1, 1), factory_fallback_cost=Decimal("180.00"), factory_workshop_id=99,
    )
    mock_publish.assert_awaited_once_with(42, "corr-xyz")


def test_place_order_rejects_past_deadline():
    workshops = AsyncMock()
    app.dependency_overrides[workshop_repo] = lambda: workshops
    app.dependency_overrides[order_repo] = lambda: AsyncMock()

    response = client.post(
        "/orders",
        json={
            "buyer_ref": "buyer-1", "product_type": "kurta", "total_qty": 10,
            "quality_min": 2, "deadline": "2020-01-01",
        },
    )

    assert response.status_code == 422
    workshops.get_factory.assert_not_called()

def test_get_order_status_404_when_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[sublot_repo] = lambda: AsyncMock()
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications()

    response = client.get("/orders/999")

    assert response.status_code == 404

def test_quote_404_when_order_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[sublot_repo] = lambda: AsyncMock()

    response = client.get("/orders/999/quote")

    assert response.status_code == 404


def test_quote_pre_allocation_uses_factory_fallback_cost():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "correlation_id": "corr-1", "product_type": "kurta", "total_qty": 100,
        "factory_fallback_cost": Decimal("180.00"),
    })
    sublots = AsyncMock()
    sublots.list_for_order.return_value = []
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.get("/orders/1/quote")

    assert response.status_code == 200
    body = response.json()
    assert body["line_items"][0]["unit_price"] == "180.00"
    assert body["line_items"][0]["subtotal"] == "18000.00"
    assert body["platform_fee"] == "900.00"
    assert body["total"] == "18900.00"


def test_quote_post_allocation_uses_weighted_average_across_sublots():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "correlation_id": "corr-1", "product_type": "kurta", "total_qty": 100,
        "factory_fallback_cost": Decimal("180.00"),
    })
    sublots = AsyncMock()
    sublots.list_for_order.return_value = [
        _mock_sublot(qty_assigned=60, cost_per_unit=Decimal("90.00")),
        _mock_sublot(qty_assigned=40, cost_per_unit=Decimal("100.00")),
    ]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.get("/orders/1/quote")

    assert response.status_code == 200
    body = response.json()
    assert body["line_items"][0]["total_qty"] == 100
    assert body["line_items"][0]["unit_price"] == "94.00"
    assert body["line_items"][0]["subtotal"] == "9400.00"
    assert body["platform_fee"] == "470.00"
    assert body["total"] == "9870.00"

def test_cancel_order_404_when_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[sublot_repo] = lambda: AsyncMock()
    app.dependency_overrides[workshop_repo] = lambda: AsyncMock()

    response = client.delete("/orders/999")

    assert response.status_code == 404


def test_cancel_order_409_when_invalid_transition():
    orders = _mock_orders({"status": "IN_PRODUCTION", "correlation_id": "corr-1", "product_type": "kurta"})
    orders.cancel.side_effect = InvalidStateTransitionError("cannot cancel in IN_PRODUCTION")
    app.dependency_overrides[order_repo] = lambda: orders
    app.dependency_overrides[sublot_repo] = lambda: AsyncMock()
    app.dependency_overrides[workshop_repo] = lambda: AsyncMock()

    response = client.delete("/orders/1")

    assert response.status_code == 409


def test_cancel_order_204_releases_capacity_per_sublot():
    orders = _mock_orders({"status": "PENDING", "correlation_id": "corr-1", "product_type": "kurta"})
    orders.cancel = AsyncMock()
    app.dependency_overrides[order_repo] = lambda: orders

    sublots = AsyncMock()
    sublots.list_for_order.return_value = [
        MagicMock(workshop_id=1, qty_assigned=30),
        MagicMock(workshop_id=2, qty_assigned=20),
    ]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    workshops = AsyncMock()
    app.dependency_overrides[workshop_repo] = lambda: workshops

    response = client.delete("/orders/1")

    assert response.status_code == 204
    orders.cancel.assert_awaited_once_with(1)
    workshops.release_capacity.assert_any_call(1, "kurta", 30)
    workshops.release_capacity.assert_any_call(2, "kurta", 20)
    assert workshops.release_capacity.await_count == 2
    sublots.cancel_for_order.assert_awaited_once_with(1)

def test_list_orders_returns_paginated_response():
    orders = AsyncMock()
    orders.list_paginated.return_value = (
        [
            {
                "order_id": 1, "status": "ALLOCATED", "product_type": "kurta",
                "total_qty": 100, "deadline": date(2026, 9, 1),
                "created_at": datetime(2026, 7, 1, 12, 0),
            },
        ],
        1,
    )
    app.dependency_overrides[order_repo] = lambda: orders

    response = client.get("/orders?status=ALLOCATED&page=1&page_size=20")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["orders"][0]["status"] == "Confirmed"
    orders.list_paginated.assert_awaited_once_with("ALLOCATED", 1, 20)


def test_list_orders_defaults_page_and_page_size():
    orders = AsyncMock()
    orders.list_paginated.return_value = ([], 0)
    app.dependency_overrides[order_repo] = lambda: orders

    response = client.get("/orders")

    assert response.status_code == 200
    assert response.json() == {"orders": [], "total": 0, "page": 1, "page_size": 20}
    orders.list_paginated.assert_awaited_once_with(None, 1, 20)

def test_invoice_404_when_order_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[payment_repo] = lambda: _mock_payments()

    response = client.get("/orders/999/invoice")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_invoice_409_when_order_not_closed():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "IN_PRODUCTION", "correlation_id": "corr-1"}
    )
    app.dependency_overrides[payment_repo] = lambda: _mock_payments()

    response = client.get("/orders/1/invoice")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORDER_NOT_SETTLED"


def test_invoice_200_recomputes_totals_from_payments():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "CLOSED", "correlation_id": "corr-1"}
    )
    app.dependency_overrides[payment_repo] = lambda: _mock_payments([
        {"base_amount": Decimal("1000.00"), "buyer_billable_amount": Decimal("1000.00")},
        {"base_amount": Decimal("500.00"), "buyer_billable_amount": Decimal("500.00")},
    ])

    response = client.get("/orders/1/invoice")

    assert response.status_code == 200
    body = response.json()
    assert body["buyer_base"] == "1500.00"
    assert body["platform_fee"] == "75.00"
    assert body["buyer_total"] == "1575.00"
    assert response.headers["X-Correlation-ID"] == "corr-1"


def test_invoice_excludes_confirmed_defect_sublots_from_buyer_base():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "CLOSED", "correlation_id": "corr-2"}
    )
    app.dependency_overrides[payment_repo] = lambda: _mock_payments([
        {"base_amount": Decimal("1000.00"), "buyer_billable_amount": Decimal("1000.00")},
        {"base_amount": Decimal("500.00"), "buyer_billable_amount": Decimal("0.00")},
    ])

    response = client.get("/orders/1/invoice")

    assert response.status_code == 200
    body = response.json()
    assert body["buyer_base"] == "1000.00"
    assert body["platform_fee"] == "50.00"
    assert body["buyer_total"] == "1050.00"

def _photo_upload():
    return {"photo": ("defect.jpg", b"\xff\xd8\xff\xe0fake-jpeg-bytes", "image/jpeg")}


def test_flag_defect_404_when_order_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post(
        "/orders/999/flag-defect",
        files=_photo_upload(),
        data={"defect_qty": "3", "description": "torn seam"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_flag_defect_409_when_coordinator_raises_invalid_transition(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "IN_PRODUCTION", "correlation_id": "corr-1"}
    )
    coordinator = AsyncMock()
    coordinator.on_defect_flagged.side_effect = InvalidStateTransitionError("no delivered sublot")
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post(
        "/orders/1/flag-defect",
        files=_photo_upload(),
        data={"defect_qty": "3", "description": "torn seam"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_DELIVERED_SUBLOT"


def test_flag_defect_202_never_exposes_sublot_or_workshop(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "IN_PRODUCTION", "correlation_id": "corr-1"}
    )
    coordinator = AsyncMock()
    coordinator.on_defect_flagged = AsyncMock(
        return_value=VerificationCompletionResult(
            status="VERIFIED", explanation="Matches the order specification."
        )
    )
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post(
        "/orders/1/flag-defect",
        files=_photo_upload(),
        data={"defect_qty": "3", "description": "torn seam"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "order_id": 1,
        "defect_qty": 3,
        "verification_status": "VERIFIED",
        "explanation": "Matches the order specification.",
        "explanations": {},
        "fault_party": None,
    }
    coordinator.on_defect_flagged.assert_awaited_once()
    call_kwargs = coordinator.on_defect_flagged.call_args.kwargs
    assert call_kwargs["order_id"] == 1
    assert call_kwargs["defect_qty"] == 3
    assert call_kwargs["description"] == "torn seam"


def test_flag_defect_202_reports_failed_status_for_confirmed_defect(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_directory", str(tmp_path))
    app.dependency_overrides[order_repo] = lambda: _mock_orders(
        {"status": "IN_PRODUCTION", "correlation_id": "corr-1"}
    )
    coordinator = AsyncMock()
    coordinator.on_defect_flagged = AsyncMock(
        return_value=VerificationCompletionResult(status="FAILED", explanation="Stitching defect visible.")
    )
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post(
        "/orders/1/flag-defect",
        files=_photo_upload(),
        data={"defect_qty": "3", "description": "torn seam"},
    )

    assert response.status_code == 202
    assert response.json()["verification_status"] == "FAILED"
    assert response.json()["explanation"] == "Stitching defect visible."

def test_get_order_status_sets_correlation_id_header():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-status", "status": "CLOSED", "total_qty": 100,
    })
    sublots = AsyncMock()
    sublots.list_for_order.return_value = []
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications()

    response = client.get("/orders/1")

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-status"


def test_get_order_status_has_defect_photo_true_when_one_exists():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-1", "status": "CLOSED", "total_qty": 100,
    })
    sublots = AsyncMock()
    sublots.list_for_order.return_value = []
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications(
        "uploads/order-defects/1/defect.jpg"
    )

    response = client.get("/orders/1")

    assert response.status_code == 200
    assert response.json()["has_defect_photo"] is True


def test_get_order_status_has_defect_photo_false_when_none_exists():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-1", "status": "IN_PRODUCTION", "total_qty": 100,
    })
    sublots = AsyncMock()
    sublots.list_for_order.return_value = []
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications(None)

    response = client.get("/orders/1")

    assert response.status_code == 200
    assert response.json()["has_defect_photo"] is False


def test_defect_photo_404_when_order_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications()

    response = client.get("/orders/999/defect-photo")

    assert response.status_code == 404


def test_defect_photo_404_when_none_on_file():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-1", "status": "CLOSED", "total_qty": 100,
    })
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications(None)

    response = client.get("/orders/1/defect-photo")

    assert response.status_code == 404


def test_defect_photo_404_when_recorded_path_missing_from_disk(tmp_path):
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-1", "status": "CLOSED", "total_qty": 100,
    })
    missing_path = str(tmp_path / "gone.jpg")
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications(missing_path)

    response = client.get("/orders/1/defect-photo")

    assert response.status_code == 404


def test_defect_photo_200_serves_the_actual_file(tmp_path):
    photo_file = tmp_path / "defect.png"
    photo_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "order_id": 1, "correlation_id": "corr-1", "status": "CLOSED", "total_qty": 100,
    })
    app.dependency_overrides[verification_repo] = lambda: _mock_verifications(str(photo_file))

    response = client.get("/orders/1/defect-photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\nfake-png-bytes"


def test_cancel_order_sets_correlation_id_header_even_on_204():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({
        "status": "PENDING", "correlation_id": "corr-cancel", "product_type": "kurta",
    })
    orders = app.dependency_overrides[order_repo]()
    orders.cancel = AsyncMock()
    app.dependency_overrides[order_repo] = lambda: orders

    sublots = AsyncMock()
    sublots.list_for_order.return_value = []
    app.dependency_overrides[sublot_repo] = lambda: sublots
    app.dependency_overrides[workshop_repo] = lambda: AsyncMock()

    response = client.delete("/orders/1")

    assert response.status_code == 204
    assert response.headers["X-Correlation-ID"] == "corr-cancel"
