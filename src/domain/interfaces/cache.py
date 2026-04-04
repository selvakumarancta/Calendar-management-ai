"""Port: Cache — abstract interface for caching layer."""

from __future__ import annotations

import abc
from typing import Any


class CachePort(abc.ABC):
    """Abstract cache interface (Redis, Memcached, in-memory, etc.)."""

    @abc.abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get a cached value by key. Returns None on miss."""
        ...

    @abc.abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Set a value with optional TTL."""
        ...

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a cached value."""
        ...

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        ...

    @abc.abstractmethod
    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter. Returns new value."""
        ...

    @abc.abstractmethod
    async def get_or_set(self, key: str, factory: Any, ttl_seconds: int = 300) -> Any:
        """Get cached value or compute & cache it via factory callable."""
        ...
