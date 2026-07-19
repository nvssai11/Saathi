from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from workers.auto_verify_worker import AutoVerifyWorker


@pytest.mark.anyio
async def test_run_calls_sweep_then_stops_on_cancel():
    coordinator = AsyncMock()
    coordinator.auto_verify_expired_deliveries = AsyncMock(return_value=2)
    worker = AutoVerifyWorker(coordinator)

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError()

    with patch("workers.auto_verify_worker.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await worker.run()

    coordinator.auto_verify_expired_deliveries.assert_awaited_once()


@pytest.mark.anyio
async def test_run_survives_sweep_exception_and_still_sleeps():
    coordinator = AsyncMock()
    coordinator.auto_verify_expired_deliveries = AsyncMock(side_effect=RuntimeError("db unavailable"))
    worker = AutoVerifyWorker(coordinator)

    async def fake_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError()

    with patch("workers.auto_verify_worker.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await worker.run()

    coordinator.auto_verify_expired_deliveries.assert_awaited_once()
