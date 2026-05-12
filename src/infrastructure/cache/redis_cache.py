"""
Redis Cache Adapter — implements CachePort using Redis.
Falls back to no-op/safe defaults when Redis is unreachable instead of crashing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError

from src.domain.interfaces.cache import CachePort

logger = logging.getLogger(__name__)


class RedisCacheAdapter(CachePort):
    """Concrete cache adapter using async Redis.

    All operations degrade gracefully when Redis is unreachable:
    - get → returns None
    - set/delete/increment → silently skipped
    - exists → returns False
    This prevents Redis downtime from causing HTTP 500s in the app.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def get(self, key: str) -> Any | None:
        try:
            value = await self._redis.get(key)
            if value is not None:
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value
            return None
        except (RedisConnectionError, RedisError, OSError) as e:
            logger.warning("Redis get failed, returning None: %s", e)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        try:
            serialized = json.dumps(value, default=str)
            await self._redis.set(key, serialized, ex=ttl_seconds)
        except (RedisConnectionError, RedisError, OSError) as e:
            logger.warning("Redis set failed, skipping: %s", e)

    async def delete(self, key: str) -> None:
        try:
            if "*" in key:
                async for k in self._redis.scan_iter(match=key):
                    await self._redis.delete(k)
            else:
                await self._redis.delete(key)
        except (RedisConnectionError, RedisError, OSError) as e:
            logger.warning("Redis delete failed, skipping: %s", e)

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._redis.exists(key))
        except (RedisConnectionError, RedisError, OSError) as e:
            logger.warning("Redis exists failed, returning False: %s", e)
            return False

    async def increment(self, key: str, amount: int = 1) -> int:
        try:
            return await self._redis.incrby(key, amount)
        except (RedisConnectionError, RedisError, OSError) as e:
            logger.warning("Redis increment failed, returning 0: %s", e)
            return 0

    async def get_or_set(self, key: str, factory: Any, ttl_seconds: int = 300) -> Any:
        value = await self.get(key)
        if value is not None:
            return value

        if callable(factory):
            result = factory()
            # Handle async callables
            if hasattr(result, "__await__"):
                result = await result
        else:
            result = factory

        await self.set(key, result, ttl_seconds)
        return result

    async def close(self) -> None:
        await self._redis.close()
