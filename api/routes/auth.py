from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import otp_service
from api.models import (
    OtpRequestRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    OtpVerifyResponse,
)
from config import settings
from core.exceptions import (
    OtpAttemptsExceededError,
    OtpExpiredError,
    OtpInvalidError,
    WorkshopNotFoundError,
)
from services.auth.otp_service import OtpService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/otp/request", response_model=OtpRequestResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_otp(
    body: OtpRequestRequest,
    otp: OtpService = Depends(otp_service),
):
    try:
        demo_code = await otp.request_otp(body.phone_number)
    except WorkshopNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PHONE_NOT_REGISTERED",
                "message": "No workshop is registered with that phone number.",
            },
        )

    return OtpRequestResponse(
        phone_number=body.phone_number,
        expires_in_seconds=settings.otp_expiry_seconds,
        demo_code=demo_code,
    )


@router.post("/otp/verify", response_model=OtpVerifyResponse)
async def verify_otp(
    body: OtpVerifyRequest,
    otp: OtpService = Depends(otp_service),
):
    try:
        token, workshop_id, workshop_name = await otp.verify_otp(body.phone_number, body.code)
    except WorkshopNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PHONE_NOT_REGISTERED",
                "message": "No workshop is registered with that phone number.",
            },
        )
    except (OtpInvalidError, OtpExpiredError, OtpAttemptsExceededError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OTP_INVALID", "message": str(exc)},
        )

    return OtpVerifyResponse(token=token, workshop_id=workshop_id, workshop_name=workshop_name)
