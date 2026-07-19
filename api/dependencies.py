from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from config import settings
from db.connection import get_pool
from db.repositories.buyer_payment_repository import BuyerPaymentRepository
from db.repositories.notification_repository import NotificationRepository
from db.repositories.order_repository import OrderRepository
from db.repositories.otp_repository import OtpRepository
from db.repositories.payment_repository import PaymentRepository
from db.repositories.sublot_repository import SublotRepository
from db.repositories.trust_repository import TrustRepository
from db.repositories.verification_repository import VerificationRepository
from db.repositories.workshop_repository import WorkshopRepository
from services.auth.otp_service import OtpService
from services.coordinator import OrderCoordinator, build_coordinator
from services.messaging.gateway import ConsoleNotificationGateway, NotificationGateway



def require_buyer(authorization: str = Header(...)) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.buyer_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Buyer token invalid")


def require_workshop(authorization: str = Header(...)) -> int:
    token = authorization.removeprefix("Bearer ").strip()
    workshop_id = settings.workshop_tokens.get(token)
    if workshop_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workshop token invalid")
    return workshop_id


def require_admin(authorization: str = Header(...)) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin token invalid")



def order_repo() -> OrderRepository:
    return OrderRepository(get_pool())


def workshop_repo() -> WorkshopRepository:
    return WorkshopRepository(get_pool())


def sublot_repo() -> SublotRepository:
    return SublotRepository(get_pool())


def trust_repo() -> TrustRepository:
    from core.trust.scorer import TrustScorer, TrustScorerConfig
    scorer = TrustScorer(TrustScorerConfig(
        window_size=settings.trust_window_size,
        cold_start_score=settings.trust_cold_start_score,
        recency_decay=settings.trust_recency_decay,
    ))
    return TrustRepository(get_pool(), scorer)


def verification_repo() -> VerificationRepository:
    return VerificationRepository(get_pool())


def payment_repo() -> PaymentRepository:
    return PaymentRepository(get_pool())


def buyer_payment_repo() -> BuyerPaymentRepository:
    return BuyerPaymentRepository(get_pool())


def notification_repo() -> NotificationRepository:
    return NotificationRepository(get_pool())


def otp_repo() -> OtpRepository:
    return OtpRepository(get_pool())


_notification_gateway: NotificationGateway = ConsoleNotificationGateway()


def notification_gateway() -> NotificationGateway:
    return _notification_gateway


def otp_service() -> OtpService:
    return OtpService(otp_repo(), workshop_repo(), notification_gateway())


_coordinator: OrderCoordinator | None = None


def set_coordinator(c: OrderCoordinator) -> None:
    global _coordinator
    _coordinator = c


def get_coordinator() -> OrderCoordinator:
    if _coordinator is None:
        raise RuntimeError("Coordinator not initialised")
    return _coordinator
