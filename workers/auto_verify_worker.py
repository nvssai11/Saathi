from __future__ import annotations

import asyncio
import logging

from config import settings
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)


class AutoVerifyWorker:

    def __init__(self, coordinator: OrderCoordinator) -> None:
        self._coordinator = coordinator

    async def run(self) -> None:
        logger.info(
            "AutoVerifyWorker started (grace_seconds=%d, sweep_interval_seconds=%d)",
            settings.verification_auto_approve_grace_seconds,
            settings.auto_verify_sweep_interval_seconds,
        )
        try:
            while True:
                try:
                    count = await self._coordinator.auto_verify_expired_deliveries()
                    if count:
                        logger.info("AutoVerifyWorker auto-verified %d sublot(s)", count)
                except Exception:
                    logger.exception("AutoVerifyWorker sweep failed — will retry next tick")
                await asyncio.sleep(settings.auto_verify_sweep_interval_seconds)
        except asyncio.CancelledError:
            logger.info("AutoVerifyWorker cancelled")
            raise
