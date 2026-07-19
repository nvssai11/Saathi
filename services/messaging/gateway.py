from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class NotificationGateway(Protocol):
    async def send(self, phone_number: str, message: str) -> None: ...


class ConsoleNotificationGateway:
    async def send(self, phone_number: str, message: str) -> None:
        logger.info("NOTIFY %s: %s", phone_number, message)
