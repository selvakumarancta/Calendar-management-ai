"""
Token Blocklist — server-side JWT invalidation.

Stores revoked JTIs (JWT IDs) in Redis with a TTL equal to the token's
remaining lifetime.  Falls back to an in-process dict when Redis is
unavailable (single-instance dev mode; not suitable for multi-replica prod
without Redis).

Usage:
  blocklist = TokenBlocklist()
  await blocklist.revoke(jti, expires_at)   # on logout / password change
  is_bad = await blocklist.is_revoked(jti)  # in get_current_user
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("calendar_agent.token_blocklist")

# In-process fallback: {jti: expiry_unix_timestamp}
_local_blocklist: dict[str, float] = {}


class TokenBlocklist:
    """Redis-backed (with in-memory fallback) JWT revocation store."""

    _PREFIX = "blocklist:jwt:"

    def __init__(self) -> None:
        self._redis: object | None = None
        self._redis_checked = False

    def _get_redis(self) -> object | None:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            import os

            import redis.asyncio as aioredis

            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            self._redis = aioredis.from_url(url, decode_responses=True)
        except Exception:
            self._redis = None
        return self._redis

    async def revoke(self, jti: str, expires_at: datetime | None = None) -> None:
        """Mark a JTI as revoked until its natural expiry."""
        now = time.time()
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            ttl = int(expires_at.timestamp() - now)
            expiry_ts = expires_at.timestamp()
        else:
            # Default: 8 hours (matches max access token lifetime)
            ttl = 8 * 3600
            expiry_ts = now + ttl

        # If the token is already expired there is nothing to revoke.
        if ttl <= 0:
            return

        redis = self._get_redis()
        if redis is not None:
            try:
                import redis.asyncio as aioredis

                r: aioredis.Redis = redis  # type: ignore[assignment]
                await r.setex(f"{self._PREFIX}{jti}", ttl, "1")
                return
            except Exception as exc:
                logger.warning("Redis blocklist write failed, using local: %s", exc)

        # In-process fallback
        _local_blocklist[jti] = expiry_ts
        # Prune stale entries to prevent unbounded growth
        _prune_local()

    async def is_revoked(self, jti: str) -> bool:
        """Return True if the JTI is in the blocklist."""
        redis = self._get_redis()
        if redis is not None:
            try:
                import redis.asyncio as aioredis

                r: aioredis.Redis = redis  # type: ignore[assignment]
                val = await r.get(f"{self._PREFIX}{jti}")
                return val is not None
            except Exception as exc:
                logger.warning("Redis blocklist read failed, using local: %s", exc)

        # In-process fallback
        entry = _local_blocklist.get(jti)
        if entry is None:
            return False
        if time.time() > entry:
            _local_blocklist.pop(jti, None)
            return False
        return True


def _prune_local() -> None:
    """Remove expired entries from the in-process store."""
    now = time.time()
    expired = [k for k, v in _local_blocklist.items() if now > v]
    for k in expired:
        _local_blocklist.pop(k, None)


# Module-level singleton — shared across all requests in the same process
_blocklist_instance: TokenBlocklist | None = None


def get_token_blocklist() -> TokenBlocklist:
    global _blocklist_instance
    if _blocklist_instance is None:
        _blocklist_instance = TokenBlocklist()
    return _blocklist_instance
