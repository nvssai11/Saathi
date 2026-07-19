from __future__ import annotations

import asyncio
import logging

from config import settings
from services.coordinator import OrderCoordinator

logger = logging.getLogger(__name__)


class StuckOrderWorker:

    def __init__(self, coordinator: OrderCoordinator) -> None:
        self._coordinator = coordinator

    async def run(self) -> None:
        logger.info(
            "StuckOrderWorker started (threshold_seconds=%d, sweep_interval_seconds=%d)",
            settings.stuck_order_threshold_seconds,
            settings.stuck_order_sweep_interval_seconds,
        )
        try:
            while True:
                try:
                    order_ids = await self._coordinator.reconcile_stuck_orders()
                    if order_ids:
                        logger.info("StuckOrderWorker republished order(s): %s", order_ids)
                except Exception:
                    logger.exception("StuckOrderWorker sweep failed — will retry next tick")
                await asyncio.sleep(settings.stuck_order_sweep_interval_seconds)
        except asyncio.CancelledError:
            logger.info("StuckOrderWorker cancelled")
            raise
