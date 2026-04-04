"""
In-Memory Cache Adapter — development fallback when Redis is not available.
Implements CachePort using a simple Python dict with TTL support.
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any

from src.domain.interfaces.cache import CachePort


class InMemoryCacheAdapter(CachePort):
    """Dict-backed cache for local development (no Redis required)."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}

    def _evict_expired(self, key: str) -> None:
        if key in self._expiry and time.time() > self._expiry[key]:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    async def get(self, key: str) -> Any | None:
        self._evict_expired(key)
        return self._store.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        self._store[key] = value
        self._expiry[key] = time.time() + ttl_seconds

    async def delete(self, key: str) -> None:
        if "*" in key:
            to_delete = [k for k in self._store if fnmatch.fnmatch(k, key)]
            for k in to_delete:
                self._store.pop(k, None)
                self._expiry.pop(k, None)
        else:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    async def exists(self, key: str) -> bool:
        self._evict_expired(key)
        return key in self._store

    async def increment(self, key: str, amount: int = 1) -> int:
        self._evict_expired(key)
        current = self._store.get(key, 0)
        new_val = int(current) + amount
        self._store[key] = new_val
        return new_val

    async def get_or_set(self, key: str, factory: Any, ttl_seconds: int = 300) -> Any:
        value = await self.get(key)
        if value is not None:
            return value
        result = factory() if callable(factory) else factory
        if hasattr(result, "__await__"):
            result = await result
        await self.set(key, result, ttl_seconds)
        return result
