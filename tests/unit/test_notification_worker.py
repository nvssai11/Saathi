from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from workers.notification_worker import NotificationWorker


@pytest.mark.anyio
async def test_handle_message_routes_to_on_sublot_assigned():
    coordinator = AsyncMock()
    worker = NotificationWorker(coordinator)

    await worker._handle_message({
        "workshop_id": 7,
        "order_id": 42,
        "sublot_id": 3,
        "product_type": "kurta",
        "qty_assigned": 60,
    })

    coordinator.on_sublot_assigned.assert_awaited_once_with(
        workshop_id=7, order_id=42, sublot_id=3, product_type="kurta", qty_assigned=60,
    )


@pytest.mark.anyio
async def test_handle_message_propagates_errors():
    coordinator = AsyncMock()
    coordinator.on_sublot_assigned.side_effect = RuntimeError("db unavailable")
    worker = NotificationWorker(coordinator)

    with pytest.raises(RuntimeError):
        await worker._handle_message({
            "workshop_id": 7,
            "order_id": 42,
            "sublot_id": 3,
            "product_type": "kurta",
            "qty_assigned": 60,
        })
