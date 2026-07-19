from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from decimal import Decimal

from api.dependencies import get_coordinator, order_repo, require_admin, sublot_repo
from api.errors import http_exception_handler
from api.routes.admin import router as admin_router
from core.exceptions import InvalidStateTransitionError

app = FastAPI()
app.add_exception_handler(HTTPException, http_exception_handler)
app.include_router(admin_router)
app.dependency_overrides[require_admin] = lambda: None

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    for dep in (order_repo, get_coordinator, sublot_repo):
        app.dependency_overrides.pop(dep, None)
    app.dependency_overrides[require_admin] = lambda: None


def _mock_orders(order_row):
    orders = AsyncMock()
    orders.get.return_value = order_row
    return orders


def test_enforce_deadline_404_when_order_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[get_coordinator] = lambda: AsyncMock()

    response = client.post("/admin/orders/999/enforce-deadline")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_enforce_deadline_409_when_deadline_not_passed():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({"status": "IN_PRODUCTION"})
    coordinator = AsyncMock()
    coordinator.enforce_deadline.side_effect = InvalidStateTransitionError("deadline not passed")
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post("/admin/orders/1/enforce-deadline")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEADLINE_NOT_PASSED"


def test_enforce_deadline_200_returns_failed_count():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({"status": "IN_PRODUCTION"})
    coordinator = AsyncMock()
    coordinator.enforce_deadline.return_value = 2
    app.dependency_overrides[get_coordinator] = lambda: coordinator

    response = client.post("/admin/orders/1/enforce-deadline")

    assert response.status_code == 200
    assert response.json() == {"order_id": 1, "sublots_marked_failed": 2}


def test_reconcile_stuck_republishes_each_order():
    orders = AsyncMock()
    orders.list_stuck_pending.return_value = [
        {"order_id": 1, "correlation_id": "aaa"},
        {"order_id": 2, "correlation_id": "bbb"},
    ]
    app.dependency_overrides[order_repo] = lambda: orders

    with patch("api.routes.admin.publish_order_placed", new=AsyncMock()) as mock_publish:
        response = client.post("/admin/orders/reconcile-stuck")

    assert response.status_code == 200
    assert response.json() == {"republished_count": 2, "order_ids": [1, 2]}
    assert mock_publish.await_count == 2
    mock_publish.assert_any_await(1, "aaa")
    mock_publish.assert_any_await(2, "bbb")


def test_reconcile_stuck_no_orders_is_a_noop():
    orders = AsyncMock()
    orders.list_stuck_pending.return_value = []
    app.dependency_overrides[order_repo] = lambda: orders

    with patch("api.routes.admin.publish_order_placed", new=AsyncMock()) as mock_publish:
        response = client.post("/admin/orders/reconcile-stuck")

    assert response.status_code == 200
    assert response.json() == {"republished_count": 0, "order_ids": []}
    mock_publish.assert_not_awaited()


def test_get_order_allocation_404_when_order_missing():
    app.dependency_overrides[order_repo] = lambda: _mock_orders(None)
    app.dependency_overrides[sublot_repo] = lambda: AsyncMock()

    response = client.get("/admin/orders/999/allocation")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_get_order_allocation_shows_the_real_multi_workshop_split():
    app.dependency_overrides[order_repo] = lambda: _mock_orders({"total_qty": 300})

    sublots = AsyncMock()
    sublots.list_for_order_admin.return_value = [
        {
            "sublot_id": 1, "workshop_id": 2, "workshop_name": "Nagpur Weaving Unit",
            "is_factory": False, "qty_assigned": 155, "delivered_qty": None,
            "cost_per_unit": Decimal("34.00"), "status": "ASSIGNED",
        },
        {
            "sublot_id": 2, "workshop_id": 5, "workshop_name": "Kolhapur Fabric Works",
            "is_factory": False, "qty_assigned": 90, "delivered_qty": None,
            "cost_per_unit": Decimal("30.95"), "status": "ASSIGNED",
        },
        {
            "sublot_id": 3, "workshop_id": 99, "workshop_name": "Central Factory (Fallback)",
            "is_factory": True, "qty_assigned": 55, "delivered_qty": None,
            "cost_per_unit": Decimal("64.00"), "status": "ASSIGNED",
        },
    ]
    app.dependency_overrides[sublot_repo] = lambda: sublots

    response = client.get("/admin/orders/87/allocation")

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == 87
    assert body["total_qty"] == 300
    assert body["workshop_count"] == 2
    assert len(body["sublots"]) == 3
    assert {s["workshop_id"] for s in body["sublots"]} == {2, 5, 99}
