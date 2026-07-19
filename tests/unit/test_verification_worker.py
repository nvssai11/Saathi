from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.exceptions import InvalidStateTransitionError
from workers.verification_worker import VerificationWorker


@pytest.mark.anyio
async def test_handle_message_routes_to_on_sublot_delivered():
    coordinator = AsyncMock()
    worker = VerificationWorker(coordinator)

    await worker._handle_message({"sublot_id": 5, "order_id": 10, "delivered_qty": 40})

    coordinator.on_sublot_delivered.assert_awaited_once_with(5, 10, 40)


@pytest.mark.anyio
async def test_handle_message_swallows_replay_guard():
    coordinator = AsyncMock()
    coordinator.on_sublot_delivered.side_effect = InvalidStateTransitionError("already DELIVERED")
    worker = VerificationWorker(coordinator)

    await worker._handle_message({"sublot_id": 1, "order_id": 1, "delivered_qty": 10})


@pytest.mark.anyio
async def test_handle_message_propagates_other_errors():
    coordinator = AsyncMock()
    coordinator.on_sublot_delivered.side_effect = RuntimeError("db unavailable")
    worker = VerificationWorker(coordinator)

    with pytest.raises(RuntimeError):
        await worker._handle_message({"sublot_id": 1, "order_id": 1, "delivered_qty": 10})
