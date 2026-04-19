"""
Unit tests for:
  - src/domain/value_objects (TimeSlot, WorkingHours, DateRange, TokenUsage)
  - src/infrastructure/security/token_encryption (encrypt/decrypt)
  - src/infrastructure/security/token_blocklist (in-memory fallback path)
"""

from __future__ import annotations

import time as _time
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from src.domain.value_objects import DateRange, TimeSlot, TokenUsage, WorkingHours
from src.infrastructure.security.token_blocklist import TokenBlocklist, _local_blocklist
from src.infrastructure.security.token_encryption import (
    decrypt_token,
    encrypt_token,
    set_encryption_key,
)

_UTC = timezone.utc
_NOW = datetime.now(_UTC)


# ===========================================================================
# TimeSlot
# ===========================================================================


class TestTimeSlot:
    @pytest.mark.unit
    def test_duration_minutes(self):
        start = _NOW
        slot = TimeSlot(start=start, end=start + timedelta(hours=2))
        assert slot.duration_minutes == 120

    @pytest.mark.unit
    def test_duration_30_minutes(self):
        start = _NOW
        slot = TimeSlot(start=start, end=start + timedelta(minutes=30))
        assert slot.duration_minutes == 30

    @pytest.mark.unit
    def test_overlaps_true(self):
        s1 = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=2))
        s2 = TimeSlot(start=_NOW + timedelta(hours=1), end=_NOW + timedelta(hours=3))
        assert s1.overlaps(s2)

    @pytest.mark.unit
    def test_overlaps_false_adjacent(self):
        s1 = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=1))
        s2 = TimeSlot(start=_NOW + timedelta(hours=1), end=_NOW + timedelta(hours=2))
        assert not s1.overlaps(s2)

    @pytest.mark.unit
    def test_overlaps_symmetric(self):
        s1 = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=2))
        s2 = TimeSlot(start=_NOW + timedelta(hours=1), end=_NOW + timedelta(hours=3))
        assert s1.overlaps(s2) == s2.overlaps(s1)

    @pytest.mark.unit
    def test_contains_midpoint(self):
        s = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=2))
        assert s.contains(_NOW + timedelta(hours=1))

    @pytest.mark.unit
    def test_contains_start_boundary(self):
        s = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=2))
        assert s.contains(_NOW)

    @pytest.mark.unit
    def test_does_not_contain_outside_point(self):
        s = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=2))
        assert not s.contains(_NOW + timedelta(hours=3))

    @pytest.mark.unit
    def test_str_representation_has_em_dash(self):
        s = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=1))
        assert "—" in str(s)

    @pytest.mark.unit
    def test_timeslot_is_frozen(self):
        s = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=1))
        with pytest.raises((AttributeError, TypeError)):
            s.start = _NOW + timedelta(hours=2)  # type: ignore[misc]


# ===========================================================================
# WorkingHours
# ===========================================================================


class TestWorkingHours:
    @pytest.mark.unit
    def test_within_working_hours(self):
        wh = WorkingHours(start=time(9, 0), end=time(17, 0))
        # Monday 10:00 UTC
        monday_10am = datetime(
            2026, 4, 20, 10, 0, tzinfo=_UTC
        )  # April 20, 2026 is a Monday
        assert wh.is_within(monday_10am)

    @pytest.mark.unit
    def test_outside_working_hours_weekend(self):
        wh = WorkingHours(start=time(9, 0), end=time(17, 0))
        # Saturday
        saturday = datetime(
            2026, 4, 18, 10, 0, tzinfo=_UTC
        )  # April 18, 2026 is Saturday
        assert not wh.is_within(saturday)

    @pytest.mark.unit
    def test_outside_working_hours_too_early(self):
        wh = WorkingHours(start=time(9, 0), end=time(17, 0))
        monday_8am = datetime(2026, 4, 20, 8, 0, tzinfo=_UTC)
        assert not wh.is_within(monday_8am)

    @pytest.mark.unit
    def test_outside_working_hours_too_late(self):
        wh = WorkingHours(start=time(9, 0), end=time(17, 0))
        monday_6pm = datetime(2026, 4, 20, 18, 0, tzinfo=_UTC)
        assert not wh.is_within(monday_6pm)


# ===========================================================================
# DateRange
# ===========================================================================


class TestDateRange:
    @pytest.mark.unit
    def test_valid_date_range(self):
        dr = DateRange(start=_NOW, end=_NOW + timedelta(days=7))
        assert dr.start < dr.end

    @pytest.mark.unit
    def test_invalid_range_raises_value_error(self):
        with pytest.raises(ValueError):
            DateRange(start=_NOW + timedelta(days=1), end=_NOW)

    @pytest.mark.unit
    def test_equal_start_end_does_not_raise(self):
        # Point-in-time range (start == end) should not raise
        dr = DateRange(start=_NOW, end=_NOW)
        assert dr.start == dr.end

    @pytest.mark.unit
    def test_frozen(self):
        dr = DateRange(start=_NOW, end=_NOW + timedelta(days=1))
        with pytest.raises((AttributeError, TypeError)):
            dr.start = _NOW  # type: ignore[misc]


# ===========================================================================
# TokenUsage
# ===========================================================================


class TestTokenUsage:
    @pytest.mark.unit
    def test_total_tokens_sum(self):
        tu = TokenUsage(prompt_tokens=100, completion_tokens=50, model="gpt-4o-mini")
        assert tu.total_tokens == 150

    @pytest.mark.unit
    def test_zero_usage(self):
        tu = TokenUsage(prompt_tokens=0, completion_tokens=0, model="gpt-4o-mini")
        assert tu.total_tokens == 0

    @pytest.mark.unit
    def test_frozen(self):
        tu = TokenUsage(prompt_tokens=10, completion_tokens=5, model="gpt-4o-mini")
        with pytest.raises((AttributeError, TypeError)):
            tu.prompt_tokens = 20  # type: ignore[misc]


# ===========================================================================
# Token Encryption
# ===========================================================================


@pytest.fixture(autouse=True)
def _reset_fernet():
    """Re-initialize fernet with a known test key before each test."""
    set_encryption_key("test-secret-key-for-encryption-tests-only")
    yield


class TestTokenEncryption:
    @pytest.mark.unit
    def test_encrypt_then_decrypt_round_trip(self):
        plain = "ya29.google-oauth2-access-token"
        cipher = encrypt_token(plain)
        assert decrypt_token(cipher) == plain

    @pytest.mark.unit
    def test_encrypted_token_has_enc_prefix(self):
        cipher = encrypt_token("some-token")
        assert cipher.startswith("enc:")

    @pytest.mark.unit
    def test_dev_token_passes_through_unchanged(self):
        assert encrypt_token("dev-token") == "dev-token"
        assert decrypt_token("dev-token") == "dev-token"

    @pytest.mark.unit
    def test_empty_string_passes_through(self):
        assert encrypt_token("") == ""
        assert decrypt_token("") == ""

    @pytest.mark.unit
    def test_plain_text_without_enc_prefix_decrypts_as_is(self):
        """Legacy tokens stored without the 'enc:' prefix should be returned as-is."""
        assert decrypt_token("plain-legacy-token") == "plain-legacy-token"

    @pytest.mark.unit
    def test_different_encryptions_of_same_plaintext_differ(self):
        """Fernet uses a random IV so two encryptions of the same value differ."""
        tok = "same-token"
        c1 = encrypt_token(tok)
        c2 = encrypt_token(tok)
        # Both should decrypt to the same value, but ciphertexts differ
        assert decrypt_token(c1) == decrypt_token(c2) == tok
        assert c1 != c2

    @pytest.mark.unit
    def test_long_token_round_trips(self):
        long_tok = "a" * 1000
        assert decrypt_token(encrypt_token(long_tok)) == long_tok

    @pytest.mark.unit
    def test_key_change_causes_decrypt_to_return_empty(self):
        """Decrypting with a different key should fail gracefully."""
        cipher = encrypt_token("ya29.secret")
        # Re-initialize with a different key
        set_encryption_key("completely-different-key")
        result = decrypt_token(cipher)
        assert result == ""


# ===========================================================================
# Token Blocklist (in-memory path — Redis not available in unit tests)
# ===========================================================================


@pytest.fixture(autouse=True)
def _clear_blocklist():
    """Ensure in-process blocklist is clean before/after each test."""
    _local_blocklist.clear()
    yield
    _local_blocklist.clear()


class TestTokenBlocklist:
    @pytest.mark.unit
    async def test_revoke_then_is_revoked_returns_true(self):
        bl = TokenBlocklist()
        bl._redis_checked = True  # force in-memory path
        bl._redis = None
        jti = str(uuid.uuid4())
        await bl.revoke(jti)
        assert await bl.is_revoked(jti)

    @pytest.mark.unit
    async def test_unknown_jti_is_not_revoked(self):
        bl = TokenBlocklist()
        bl._redis_checked = True
        bl._redis = None
        assert not await bl.is_revoked(str(uuid.uuid4()))

    @pytest.mark.unit
    async def test_revoke_with_future_expiry(self):
        bl = TokenBlocklist()
        bl._redis_checked = True
        bl._redis = None
        jti = str(uuid.uuid4())
        expiry = datetime.now(_UTC) + timedelta(hours=1)
        await bl.revoke(jti, expires_at=expiry)
        assert await bl.is_revoked(jti)

    @pytest.mark.unit
    async def test_revoke_already_expired_token_not_stored(self):
        bl = TokenBlocklist()
        bl._redis_checked = True
        bl._redis = None
        jti = str(uuid.uuid4())
        expiry = datetime.now(_UTC) - timedelta(hours=1)
        await bl.revoke(jti, expires_at=expiry)
        # Already expired — should not be in the store
        assert not await bl.is_revoked(jti)

    @pytest.mark.unit
    async def test_different_jtis_are_independent(self):
        bl = TokenBlocklist()
        bl._redis_checked = True
        bl._redis = None
        jti_a = str(uuid.uuid4())
        jti_b = str(uuid.uuid4())
        await bl.revoke(jti_a)
        assert await bl.is_revoked(jti_a)
        assert not await bl.is_revoked(jti_b)
