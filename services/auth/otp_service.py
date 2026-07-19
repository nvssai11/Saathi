from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from config import settings
from core.exceptions import (
    OtpAttemptsExceededError,
    OtpExpiredError,
    OtpInvalidError,
    WorkshopNotFoundError,
)
from db.repositories.otp_repository import OtpRepository
from db.repositories.workshop_repository import WorkshopRepository
from services.messaging.gateway import NotificationGateway

logger = logging.getLogger(__name__)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class OtpService:
    def __init__(
        self,
        otp_repo: OtpRepository,
        workshop_repo: WorkshopRepository,
        gateway: NotificationGateway,
    ) -> None:
        self._otps = otp_repo
        self._workshops = workshop_repo
        self._gateway = gateway

    async def request_otp(self, phone_number: str) -> str | None:
        workshop = await self._workshops.get_by_phone(phone_number)
        if workshop is None:
            raise WorkshopNotFoundError(f"No workshop registered for {phone_number}")

        code = "".join(secrets.choice("0123456789") for _ in range(settings.otp_code_length))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.otp_expiry_seconds)
        await self._otps.create(phone_number, _hash_code(code), expires_at)

        await self._gateway.send(
            phone_number,
            f"Your Saathi login code is {code}. It expires in "
            f"{settings.otp_expiry_seconds // 60} minutes.",
        )
        logger.info("OTP requested for %s", phone_number)

        return code if settings.otp_demo_reveal_code else None

    async def verify_otp(self, phone_number: str, code: str) -> tuple[str, int, str]:
        workshop = await self._workshops.get_by_phone(phone_number)
        if workshop is None:
            raise WorkshopNotFoundError(f"No workshop registered for {phone_number}")

        otp = await self._otps.get_latest_active(phone_number)
        if otp is None:
            raise OtpInvalidError("No active code for this phone number — request a new one")

        if otp["expires_at"] < datetime.now(timezone.utc):
            raise OtpExpiredError("This code has expired — request a new one")

        if otp["attempts"] >= settings.otp_max_attempts:
            raise OtpAttemptsExceededError("Too many wrong attempts — request a new code")

        if otp["code_hash"] != _hash_code(code):
            await self._otps.increment_attempts(otp["otp_id"])
            raise OtpInvalidError("That code isn't right")

        await self._otps.consume(otp["otp_id"])

        workshop_id = workshop["workshop_id"]
        token = next(
            (t for t, wid in settings.workshop_tokens.items() if wid == workshop_id),
            None,
        )
        if token is None:
            logger.error("No bearer token configured for workshop_id=%d", workshop_id)
            raise WorkshopNotFoundError("This workshop isn't fully configured yet — contact support")

        return token, workshop_id, workshop["name"]
