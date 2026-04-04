"""
Usage Tracker — Redis-backed implementation of UsageTrackerPort.
Tracks per-tenant API requests and token consumption for billing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.domain.interfaces.cache import CachePort
from src.domain.interfaces.usage_tracker import UsageTrackerPort
from src.domain.value_objects import TokenUsage


class RedisUsageTracker(UsageTrackerPort):
    """
    Tracks usage via Redis counters.
    Keys: usage:{user_id}:{YYYY-MM}:requests, usage:{user_id}:{YYYY-MM}:tokens
    """

    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    def _month_key(self, user_id: UUID, suffix: str) -> str:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        return f"usage:{user_id}:{month}:{suffix}"

    async def record_request(
        self, user_id: UUID, token_usage: TokenUsage | None = None
    ) -> None:
        await self._cache.increment(self._month_key(user_id, "requests"))

        if token_usage:
            await self._cache.increment(
                self._month_key(user_id, "tokens"),
                token_usage.total_tokens,
            )
            # Track cost
            cost_key = self._month_key(user_id, "cost_micro_usd")
            cost_micro = int(token_usage.estimated_cost_usd * 1_000_000)
            if cost_micro > 0:
                await self._cache.increment(cost_key, cost_micro)

    async def get_monthly_request_count(self, user_id: UUID) -> int:
        count = await self._cache.get(self._month_key(user_id, "requests"))
        return int(count) if count else 0

    async def get_monthly_token_usage(self, user_id: UUID) -> int:
        tokens = await self._cache.get(self._month_key(user_id, "tokens"))
        return int(tokens) if tokens else 0

    async def is_within_quota(self, user_id: UUID, limit: int) -> bool:
        current = await self.get_monthly_request_count(user_id)
        return current < limit

    async def get_monthly_cost_estimate(self, user_id: UUID) -> float:
        cost_micro = await self._cache.get(self._month_key(user_id, "cost_micro_usd"))
        return int(cost_micro) / 1_000_000 if cost_micro else 0.0
