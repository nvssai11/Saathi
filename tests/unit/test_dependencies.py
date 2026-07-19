from __future__ import annotations

import pytest
from fastapi import HTTPException

from unittest.mock import MagicMock

from api.dependencies import get_coordinator, require_admin, require_buyer, require_workshop, set_coordinator
from config import settings

def test_require_buyer_accepts_correct_token():
    require_buyer(authorization=f"Bearer {settings.buyer_token}")


def test_require_buyer_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc_info:
        require_buyer(authorization="Bearer wrong-token")
    assert exc_info.value.status_code == 403


def test_require_buyer_rejects_workshop_token():
    a_workshop_token = next(iter(settings.workshop_tokens))
    with pytest.raises(HTTPException) as exc_info:
        require_buyer(authorization=f"Bearer {a_workshop_token}")
    assert exc_info.value.status_code == 403


def test_require_buyer_strips_bearer_prefix_and_whitespace():
    require_buyer(authorization=f"Bearer  {settings.buyer_token}  ")


def test_require_buyer_rejects_missing_bearer_prefix():
    with pytest.raises(HTTPException):
        require_buyer(authorization="NotBearer garbage")

def test_require_workshop_accepts_valid_token_and_returns_correct_id():
    token, expected_id = next(iter(settings.workshop_tokens.items()))
    result = require_workshop(authorization=f"Bearer {token}")
    assert result == expected_id


def test_require_workshop_rejects_unknown_token():
    with pytest.raises(HTTPException) as exc_info:
        require_workshop(authorization="Bearer not-a-real-token")
    assert exc_info.value.status_code == 403


def test_require_workshop_rejects_buyer_token():
    with pytest.raises(HTTPException) as exc_info:
        require_workshop(authorization=f"Bearer {settings.buyer_token}")
    assert exc_info.value.status_code == 403


def test_require_workshop_each_token_maps_to_its_own_id():
    for token, expected_id in settings.workshop_tokens.items():
        assert require_workshop(authorization=f"Bearer {token}") == expected_id

def test_require_admin_accepts_correct_token():
    require_admin(authorization=f"Bearer {settings.admin_token}")


def test_require_admin_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc_info:
        require_admin(authorization="Bearer wrong-token")
    assert exc_info.value.status_code == 403


def test_require_admin_rejects_buyer_token():
    with pytest.raises(HTTPException) as exc_info:
        require_admin(authorization=f"Bearer {settings.buyer_token}")
    assert exc_info.value.status_code == 403

def test_get_coordinator_raises_when_uninitialised():
    import api.dependencies as deps
    original = deps._coordinator
    deps._coordinator = None
    try:
        with pytest.raises(RuntimeError):
            get_coordinator()
    finally:
        deps._coordinator = original


def test_set_coordinator_then_get_coordinator_returns_same_instance():
    import api.dependencies as deps
    original = deps._coordinator
    fake = MagicMock()
    try:
        set_coordinator(fake)
        assert get_coordinator() is fake
    finally:
        deps._coordinator = original
