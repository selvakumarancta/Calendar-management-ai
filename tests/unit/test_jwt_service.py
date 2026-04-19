"""
Unit tests for JWTService (infrastructure/auth/jwt_service.py).

Pure unit tests — no DB, network, or async needed.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from src.domain.entities.user import SubscriptionPlan, User
from src.domain.exceptions import AuthenticationError
from src.infrastructure.auth.jwt_service import JWTService

_SECRET = "test-secret-key-not-for-prod"
_ALGO = "HS256"


def _svc(access_minutes: int = 30, refresh_days: int = 7) -> JWTService:
    return JWTService(
        secret_key=_SECRET,
        algorithm=_ALGO,
        access_token_expire_minutes=access_minutes,
        refresh_token_expire_days=refresh_days,
    )


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="alice@example.com",
        name="Alice",
        timezone="UTC",
        plan=SubscriptionPlan.PRO,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------


class TestCreateAccessToken:
    @pytest.mark.unit
    def test_returns_non_empty_string(self):
        tok = _svc().create_access_token(_user())
        assert isinstance(tok, str) and tok

    @pytest.mark.unit
    def test_payload_sub_is_user_id(self):
        u = _user()
        tok = _svc().create_access_token(u)
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert payload["sub"] == str(u.id)

    @pytest.mark.unit
    def test_payload_email_is_user_email(self):
        u = _user()
        tok = _svc().create_access_token(u)
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert payload["email"] == u.email

    @pytest.mark.unit
    def test_payload_type_is_access(self):
        tok = _svc().create_access_token(_user())
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert payload["type"] == "access"

    @pytest.mark.unit
    def test_payload_plan_matches_user_plan(self):
        u = _user()
        tok = _svc().create_access_token(u)
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert payload["plan"] == u.plan.value

    @pytest.mark.unit
    def test_payload_has_unique_jti(self):
        u = _user()
        svc = _svc()
        tok1 = svc.create_access_token(u)
        tok2 = svc.create_access_token(u)
        p1 = jwt.decode(tok1, _SECRET, algorithms=[_ALGO])
        p2 = jwt.decode(tok2, _SECRET, algorithms=[_ALGO])
        assert p1["jti"] != p2["jti"]

    @pytest.mark.unit
    def test_token_expires_after_configured_minutes(self):
        svc = _svc(access_minutes=15)
        tok = svc.create_access_token(_user())
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        now = time.time()
        assert payload["exp"] > now
        assert payload["exp"] <= now + 15 * 60 + 5  # 5 s tolerance

    @pytest.mark.unit
    def test_different_users_produce_different_tokens(self):
        svc = _svc()
        u1 = _user()
        u2 = _user()
        assert svc.create_access_token(u1) != svc.create_access_token(u2)


# ---------------------------------------------------------------------------
# create_refresh_token
# ---------------------------------------------------------------------------


class TestCreateRefreshToken:
    @pytest.mark.unit
    def test_payload_type_is_refresh(self):
        tok = _svc().create_refresh_token(_user())
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert payload["type"] == "refresh"

    @pytest.mark.unit
    def test_refresh_expires_later_than_access(self):
        svc = _svc(access_minutes=30, refresh_days=7)
        u = _user()
        a_tok = svc.create_access_token(u)
        r_tok = svc.create_refresh_token(u)
        a_payload = jwt.decode(a_tok, _SECRET, algorithms=[_ALGO])
        r_payload = jwt.decode(r_tok, _SECRET, algorithms=[_ALGO])
        assert r_payload["exp"] > a_payload["exp"]

    @pytest.mark.unit
    def test_refresh_has_no_email_claim(self):
        tok = _svc().create_refresh_token(_user())
        payload = jwt.decode(tok, _SECRET, algorithms=[_ALGO])
        assert "email" not in payload

    @pytest.mark.unit
    def test_refresh_has_unique_jti(self):
        u = _user()
        svc = _svc()
        t1 = svc.create_refresh_token(u)
        t2 = svc.create_refresh_token(u)
        p1 = jwt.decode(t1, _SECRET, algorithms=[_ALGO])
        p2 = jwt.decode(t2, _SECRET, algorithms=[_ALGO])
        assert p1["jti"] != p2["jti"]


# ---------------------------------------------------------------------------
# decode_token
# ---------------------------------------------------------------------------


class TestDecodeToken:
    @pytest.mark.unit
    def test_decode_valid_access_token(self):
        svc = _svc()
        u = _user()
        tok = svc.create_access_token(u)
        payload = svc.decode_token(tok)
        assert payload["sub"] == str(u.id)

    @pytest.mark.unit
    def test_decode_invalid_token_raises_authentication_error(self):
        svc = _svc()
        with pytest.raises(AuthenticationError):
            svc.decode_token("this.is.not.a.valid.jwt")

    @pytest.mark.unit
    def test_decode_wrong_secret_raises_authentication_error(self):
        # Create a token with a different secret
        payload = {"sub": str(uuid.uuid4()), "exp": time.time() + 3600}
        tok = jwt.encode(payload, "wrong-secret", algorithm=_ALGO)
        svc = _svc()
        with pytest.raises(AuthenticationError):
            svc.decode_token(tok)

    @pytest.mark.unit
    def test_decode_expired_token_raises_authentication_error(self):
        svc = _svc()
        # Create with 1 minute expiry, then manually forge an expired payload
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iat": past,
            "exp": past + timedelta(minutes=1),
        }
        tok = jwt.encode(payload, _SECRET, algorithm=_ALGO)
        with pytest.raises(AuthenticationError):
            svc.decode_token(tok)

    @pytest.mark.unit
    def test_decode_returns_all_standard_claims(self):
        svc = _svc()
        u = _user()
        tok = svc.create_access_token(u)
        payload = svc.decode_token(tok)
        for claim in ("sub", "email", "plan", "type", "jti", "iat", "exp"):
            assert claim in payload, f"Missing claim: {claim}"
