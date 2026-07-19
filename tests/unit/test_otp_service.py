from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from config import settings
from core.exceptions import (
    OtpAttemptsExceededError,
    OtpExpiredError,
    OtpInvalidError,
    WorkshopNotFoundError,
)
from services.auth.otp_service import OtpService, _hash_code

PHONE = "+919810000001"


def _make_service(**overrides):
    defaults = dict(
        otp_repo=AsyncMock(),
        workshop_repo=AsyncMock(),
        gateway=AsyncMock(),
    )
    defaults.update(overrides)
    return OtpService(**defaults), defaults


@pytest.fixture(autouse=True)
def _fixed_tokens(monkeypatch):
    monkeypatch.setattr(
        settings,
        "workshop_tokens_json",
        json.dumps({"token-ws-1": 1, "token-factory": 99}),
    )


@pytest.mark.anyio
async def test_request_otp_unknown_phone_raises_and_sends_nothing():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = None

    with pytest.raises(WorkshopNotFoundError):
        await service.request_otp(PHONE)

    deps["otp_repo"].create.assert_not_called()
    deps["gateway"].send.assert_not_called()


@pytest.mark.anyio
async def test_request_otp_known_phone_creates_and_sends_code(monkeypatch):
    monkeypatch.setattr(settings, "otp_demo_reveal_code", True)
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "Pune Textile Cluster A"}

    code = await service.request_otp(PHONE)

    assert code is not None and len(code) == settings.otp_code_length
    deps["otp_repo"].create.assert_awaited_once()
    args = deps["otp_repo"].create.call_args.args
    assert args[0] == PHONE
    assert args[1] == _hash_code(code)
    deps["gateway"].send.assert_awaited_once()
    assert deps["gateway"].send.call_args.args[0] == PHONE


@pytest.mark.anyio
async def test_request_otp_demo_reveal_off_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "otp_demo_reveal_code", False)
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "Pune Textile Cluster A"}

    code = await service.request_otp(PHONE)

    assert code is None


@pytest.mark.anyio
async def test_verify_otp_unknown_phone_raises():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = None

    with pytest.raises(WorkshopNotFoundError):
        await service.verify_otp(PHONE, "123456")


@pytest.mark.anyio
async def test_verify_otp_no_active_otp_raises_invalid():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "WS"}
    deps["otp_repo"].get_latest_active.return_value = None

    with pytest.raises(OtpInvalidError):
        await service.verify_otp(PHONE, "123456")


@pytest.mark.anyio
async def test_verify_otp_expired_raises():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "WS"}
    deps["otp_repo"].get_latest_active.return_value = {
        "otp_id": 1,
        "code_hash": _hash_code("123456"),
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        "attempts": 0,
    }

    with pytest.raises(OtpExpiredError):
        await service.verify_otp(PHONE, "123456")


@pytest.mark.anyio
async def test_verify_otp_attempts_exceeded_raises(monkeypatch):
    monkeypatch.setattr(settings, "otp_max_attempts", 5)
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "WS"}
    deps["otp_repo"].get_latest_active.return_value = {
        "otp_id": 1,
        "code_hash": _hash_code("123456"),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "attempts": 5,
    }

    with pytest.raises(OtpAttemptsExceededError):
        await service.verify_otp(PHONE, "123456")


@pytest.mark.anyio
async def test_verify_otp_wrong_code_increments_attempts_and_raises():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "WS"}
    deps["otp_repo"].get_latest_active.return_value = {
        "otp_id": 7,
        "code_hash": _hash_code("123456"),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "attempts": 0,
    }

    with pytest.raises(OtpInvalidError):
        await service.verify_otp(PHONE, "000000")

    deps["otp_repo"].increment_attempts.assert_awaited_once_with(7)
    deps["otp_repo"].consume.assert_not_called()


@pytest.mark.anyio
async def test_verify_otp_correct_code_consumes_and_returns_token():
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {
        "workshop_id": 1,
        "name": "Pune Textile Cluster A",
    }
    deps["otp_repo"].get_latest_active.return_value = {
        "otp_id": 7,
        "code_hash": _hash_code("123456"),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "attempts": 0,
    }

    token, workshop_id, workshop_name = await service.verify_otp(PHONE, "123456")

    assert token == "token-ws-1"
    assert workshop_id == 1
    assert workshop_name == "Pune Textile Cluster A"
    deps["otp_repo"].consume.assert_awaited_once_with(7)


@pytest.mark.anyio
async def test_verify_otp_correct_code_but_no_configured_token_raises(monkeypatch):
    monkeypatch.setattr(settings, "workshop_tokens_json", json.dumps({"token-factory": 99}))
    service, deps = _make_service()
    deps["workshop_repo"].get_by_phone.return_value = {"workshop_id": 1, "name": "WS"}
    deps["otp_repo"].get_latest_active.return_value = {
        "otp_id": 7,
        "code_hash": _hash_code("123456"),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "attempts": 0,
    }

    with pytest.raises(WorkshopNotFoundError):
        await service.verify_otp(PHONE, "123456")
