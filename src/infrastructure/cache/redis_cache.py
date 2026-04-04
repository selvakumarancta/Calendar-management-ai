"""
Redis Cache Adapter — implements CachePort using Redis.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import redis.asyncio as aioredis

from src.domain.interfaces.cache import CachePort


class RedisCacheAdapter(CachePort):
    """Concrete cache adapter using async Redis."""

    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def get(self, key: str) -> Any | None:
        value = await self._redis.get(key)
        if value is not None:
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        serialized = json.dumps(value, default=str)
        await self._redis.set(key, serialized, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        # Support wildcard deletion
        if "*" in key:
            async for k in self._redis.scan_iter(match=key):
                await self._redis.delete(k)
        else:
            await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def increment(self, key: str, amount: int = 1) -> int:
        return await self._redis.incrby(key, amount)

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
