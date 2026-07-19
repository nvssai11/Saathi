from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.domain import SublotAssignment
from core.exceptions import InvalidStateTransitionError
from workers.allocation_worker import AllocationWorker


@pytest.mark.anyio
async def test_handle_message_routes_to_on_order_placed():
    coordinator = AsyncMock()
    coordinator.on_order_placed.return_value = []
    worker = AllocationWorker(coordinator)

    await worker._handle_message({"order_id": 42, "correlation_id": "abc"})

    coordinator.on_order_placed.assert_awaited_once_with(42)


@pytest.mark.anyio
async def test_handle_message_swallows_replay_guard():
    coordinator = AsyncMock()
    coordinator.on_order_placed.side_effect = InvalidStateTransitionError("already ALLOCATED")
    worker = AllocationWorker(coordinator)

    await worker._handle_message({"order_id": 1})


@pytest.mark.anyio
async def test_handle_message_propagates_other_errors():
    coordinator = AsyncMock()
    coordinator.on_order_placed.side_effect = RuntimeError("db unavailable")
    worker = AllocationWorker(coordinator)

    with pytest.raises(RuntimeError):
        await worker._handle_message({"order_id": 1})


@pytest.mark.anyio
async def test_handle_message_publishes_sublot_assigned_per_returned_assignment():
    coordinator = AsyncMock()
    coordinator.on_order_placed.return_value = [
        SublotAssignment(sublot_id=1, order_id=42, workshop_id=7, product_type="kurta", qty_assigned=60),
        SublotAssignment(sublot_id=2, order_id=42, workshop_id=8, product_type="kurta", qty_assigned=40),
    ]
    worker = AllocationWorker(coordinator)

    with patch("workers.allocation_worker.publish_sublot_assigned", new=AsyncMock()) as mock_publish:
        await worker._handle_message({"order_id": 42})

    assert mock_publish.await_count == 2
    mock_publish.assert_any_await(sublot_id=1, order_id=42, workshop_id=7, product_type="kurta", qty_assigned=60)
    mock_publish.assert_any_await(sublot_id=2, order_id=42, workshop_id=8, product_type="kurta", qty_assigned=40)


@pytest.mark.anyio
async def test_handle_message_publishes_nothing_when_no_assignments():
    coordinator = AsyncMock()
    coordinator.on_order_placed.return_value = []
    worker = AllocationWorker(coordinator)

    with patch("workers.allocation_worker.publish_sublot_assigned", new=AsyncMock()) as mock_publish:
        await worker._handle_message({"order_id": 42})

    mock_publish.assert_not_awaited()
