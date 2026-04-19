"""
Unit tests for TokenBlocklist — tests the in-process fallback path.
No real Redis connection required (Redis import is mocked or absent).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.security.token_blocklist import (
    TokenBlocklist,
    _local_blocklist,
    _prune_local,
    get_token_blocklist,
)


def _fresh_blocklist() -> TokenBlocklist:
    """Return a TokenBlocklist that is forced to use the in-process store."""
    bl = TokenBlocklist()
    bl._redis_checked = True  # skip Redis probe
    bl._redis = None  # force in-memory path
    return bl


@pytest.fixture(autouse=True)
def _clear_local_store():
    """Wipe the module-level in-process store before each test."""
    _local_blocklist.clear()
    yield
    _local_blocklist.clear()


class TestRevokeInProcess:
    @pytest.mark.unit
    async def test_revoke_adds_to_local_store(self):
        bl = _fresh_blocklist()
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        await bl.revoke("jti-abc", expires_at=expiry)
        assert "jti-abc" in _local_blocklist

    @pytest.mark.unit
    async def test_revoke_with_no_expiry_defaults_to_8h(self):
        bl = _fresh_blocklist()
        await bl.revoke("jti-default")
        assert "jti-default" in _local_blocklist
        # TTL should be roughly 8 hours from now
        stored_ts = _local_blocklist["jti-default"]
        expected_min = time.time() + 7 * 3600
        assert stored_ts >= expected_min

    @pytest.mark.unit
    async def test_revoke_already_expired_token_is_noop(self):
        bl = _fresh_blocklist()
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        await bl.revoke("jti-past", expires_at=past)
        # Should NOT be added (ttl <= 0)
        assert "jti-past" not in _local_blocklist

    @pytest.mark.unit
    async def test_revoke_naive_datetime_treated_as_utc(self):
        bl = _fresh_blocklist()
        naive_future = datetime.utcnow() + timedelta(hours=2)
        await bl.revoke("jti-naive", expires_at=naive_future)
        assert "jti-naive" in _local_blocklist


class TestIsRevokedInProcess:
    @pytest.mark.unit
    async def test_is_revoked_returns_true_for_revoked_jti(self):
        bl = _fresh_blocklist()
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        await bl.revoke("jti-1", expires_at=expiry)
        assert await bl.is_revoked("jti-1") is True

    @pytest.mark.unit
    async def test_is_revoked_returns_false_for_unknown_jti(self):
        bl = _fresh_blocklist()
        assert await bl.is_revoked("jti-unknown") is False

    @pytest.mark.unit
    async def test_is_revoked_cleans_up_expired_entry(self):
        bl = _fresh_blocklist()
        # Insert an already-expired entry directly
        _local_blocklist["jti-stale"] = time.time() - 1  # expired 1 second ago
        result = await bl.is_revoked("jti-stale")
        assert result is False
        assert "jti-stale" not in _local_blocklist  # pruned


class TestPruneLocal:
    @pytest.mark.unit
    def test_prune_removes_expired_entries(self):
        _local_blocklist["expired"] = time.time() - 10
        _local_blocklist["valid"] = time.time() + 3600
        _prune_local()
        assert "expired" not in _local_blocklist
        assert "valid" in _local_blocklist

    @pytest.mark.unit
    def test_prune_empty_store_no_error(self):
        _prune_local()  # Should not raise


class TestGetTokenBlocklist:
    @pytest.mark.unit
    def test_returns_singleton(self):
        import src.infrastructure.security.token_blocklist as mod

        mod._blocklist_instance = None  # reset singleton
        bl1 = get_token_blocklist()
        bl2 = get_token_blocklist()
        assert bl1 is bl2
        mod._blocklist_instance = None  # clean up

    @pytest.mark.unit
    def test_returns_token_blocklist_instance(self):
        import src.infrastructure.security.token_blocklist as mod

        mod._blocklist_instance = None
        bl = get_token_blocklist()
        assert isinstance(bl, TokenBlocklist)
        mod._blocklist_instance = None


class TestGetRedisExceptionHandling:
    @pytest.mark.unit
    def test_redis_import_failure_sets_none(self):
        """Lines 47-48: when redis.asyncio import raises, _redis stays None."""
        import sys
        import unittest.mock as _mock

        bl = TokenBlocklist()
        # Simulate redis not installed by replacing the module in sys.modules
        with _mock.patch.dict("sys.modules", {"redis": None, "redis.asyncio": None}):
            result = bl._get_redis()
        # Should return None, not raise
        assert result is None
        assert bl._redis is None

    @pytest.mark.unit
    async def test_redis_write_failure_falls_back_to_local(self):
        """Lines 76-77: Redis setex raises → fallback to _local_blocklist."""
        from unittest.mock import AsyncMock

        bl = TokenBlocklist()
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(side_effect=RuntimeError("redis down"))
        bl._redis = mock_redis
        bl._redis_checked = True

        jti = "test-redis-fail-jti"
        from datetime import timedelta

        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await bl.revoke(jti, expires_at=expires)
        # Should fall back to local store without raising
        assert jti in _local_blocklist
