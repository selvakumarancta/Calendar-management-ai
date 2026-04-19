"""
Unit tests for RedisCacheAdapter.

All Redis calls are mocked with AsyncMock — no Redis server required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.cache.redis_cache import RedisCacheAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> tuple[RedisCacheAdapter, MagicMock]:
    """Return (adapter, mock_redis) with all Redis methods pre-mocked."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=1)
    mock_redis.incrby = AsyncMock(return_value=1)
    mock_redis.close = AsyncMock()

    with patch(
        "src.infrastructure.cache.redis_cache.aioredis.from_url",
        return_value=mock_redis,
    ):
        adapter = RedisCacheAdapter("redis://localhost:6379/0")

    return adapter, mock_redis


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_deserialized_json():
    """get returns the deserialized JSON value when key exists (lines 26-31)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=json.dumps({"hello": "world"}))
    result = await adapter.get("my-key")
    assert result == {"hello": "world"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_raw_string_on_json_error():
    """get returns the raw value when JSON decode fails (line 31)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value="not-json")
    result = await adapter.get("my-key")
    assert result == "not-json"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_none_when_key_missing():
    """get returns None when key does not exist (line 32)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=None)
    result = await adapter.get("missing")
    assert result is None


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_serializes_and_stores():
    """set serializes value and calls redis.set with TTL (lines 35-36)."""
    adapter, mock_redis = _make_adapter()
    await adapter.set("k", {"data": 42}, ttl_seconds=60)
    mock_redis.set.assert_called_once_with(
        "k", json.dumps({"data": 42}, default=str), ex=60
    )


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_exact_key():
    """delete calls redis.delete directly for exact keys (line 44)."""
    adapter, mock_redis = _make_adapter()
    await adapter.delete("exact-key")
    mock_redis.delete.assert_called_once_with("exact-key")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_wildcard_scans_and_deletes():
    """delete with '*' uses scan_iter then deletes each match (lines 41-42)."""
    adapter, mock_redis = _make_adapter()

    async def fake_scan_iter(match):
        for k in ["prefix:1", "prefix:2"]:
            yield k

    mock_redis.scan_iter = fake_scan_iter
    mock_redis.delete = AsyncMock()
    await adapter.delete("prefix:*")
    assert mock_redis.delete.await_count == 2


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exists_returns_true_when_key_present():
    """exists returns True when Redis returns 1 (line 47)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.exists = AsyncMock(return_value=1)
    assert await adapter.exists("k") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exists_returns_false_when_key_absent():
    """exists returns False when Redis returns 0 (line 47)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.exists = AsyncMock(return_value=0)
    assert await adapter.exists("k") is False


# ---------------------------------------------------------------------------
# increment
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_increment_returns_new_value():
    """increment calls incrby and returns the new count (line 50)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.incrby = AsyncMock(return_value=5)
    result = await adapter.increment("counter", amount=3)
    mock_redis.incrby.assert_called_once_with("counter", 3)
    assert result == 5


# ---------------------------------------------------------------------------
# get_or_set
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_set_returns_cached_value():
    """get_or_set returns existing value without calling factory (lines 53-55)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=json.dumps("cached"))
    factory_called = []

    def factory():
        factory_called.append(True)
        return "fresh"

    result = await adapter.get_or_set("k", factory)
    assert result == "cached"
    assert not factory_called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_set_calls_sync_factory_when_miss():
    """get_or_set calls sync factory and sets result on cache miss (lines 57-65)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    result = await adapter.get_or_set("k", lambda: "computed", ttl_seconds=30)
    assert result == "computed"
    mock_redis.set.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_set_calls_async_factory_when_miss():
    """get_or_set awaits async factory when result has __await__ (lines 60-61)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    async def async_factory():
        return "async-result"

    result = await adapter.get_or_set("k", async_factory)
    assert result == "async-result"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_set_uses_literal_value_when_not_callable():
    """get_or_set uses factory as literal value when not callable (lines 62-63)."""
    adapter, mock_redis = _make_adapter()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    result = await adapter.get_or_set("k", "literal-value")
    assert result == "literal-value"


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_calls_redis_close():
    """close delegates to redis.close (line 69)."""
    adapter, mock_redis = _make_adapter()
    await adapter.close()
    mock_redis.close.assert_called_once()
