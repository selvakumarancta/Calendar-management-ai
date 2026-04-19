"""
Unit tests for:
  - src/billing/plans.py          (PlanTier, PlanDefinition, PLANS catalog)
  - src/infrastructure/cache/in_memory_cache.py  (InMemoryCacheAdapter)
  - src/domain/events/__init__.py  (domain events dataclasses)
  - src/billing/usage_tracker.py   (RedisUsageTracker via mock cache)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.plans import PLANS, PlanDefinition, PlanTier
from src.billing.usage_tracker import RedisUsageTracker
from src.domain.events import (
    ConversationStarted,
    DomainEvent,
    EventConflictDetected,
    EventCreated,
    EventDeleted,
    EventUpdated,
    UserQuotaExceeded,
)
from src.domain.value_objects import TokenUsage
from src.infrastructure.cache.in_memory_cache import InMemoryCacheAdapter

# ===========================================================================
# PLANS catalog
# ===========================================================================


class TestPlansCatalog:
    @pytest.mark.unit
    def test_all_four_tiers_present(self):
        for tier in PlanTier:
            assert tier in PLANS, f"Missing plan for tier: {tier}"

    @pytest.mark.unit
    def test_each_plan_is_plan_definition(self):
        for tier, plan in PLANS.items():
            assert isinstance(plan, PlanDefinition), f"{tier} is not a PlanDefinition"

    @pytest.mark.unit
    def test_free_plan_has_zero_price(self):
        assert PLANS[PlanTier.FREE].monthly_price_usd == 0.0

    @pytest.mark.unit
    def test_pro_plan_allows_primary_model(self):
        assert PLANS[PlanTier.PRO].allows_primary_model is True

    @pytest.mark.unit
    def test_free_plan_forbids_primary_model(self):
        assert PLANS[PlanTier.FREE].allows_primary_model is False

    @pytest.mark.unit
    def test_plans_have_increasing_request_limits(self):
        limits = [
            PLANS[t].monthly_request_limit
            for t in (
                PlanTier.FREE,
                PlanTier.PRO,
                PlanTier.BUSINESS,
                PlanTier.ENTERPRISE,
            )
        ]
        assert limits == sorted(limits), "Plan limits should increase with tier"

    @pytest.mark.unit
    def test_plans_have_increasing_max_calendars(self):
        calendars = [
            PLANS[t].max_calendars
            for t in (
                PlanTier.FREE,
                PlanTier.PRO,
                PlanTier.BUSINESS,
                PlanTier.ENTERPRISE,
            )
        ]
        assert calendars == sorted(calendars)

    @pytest.mark.unit
    def test_every_plan_has_at_least_one_feature(self):
        for tier, plan in PLANS.items():
            assert len(plan.features) > 0, f"{tier} has no features"

    @pytest.mark.unit
    def test_enterprise_has_100k_requests(self):
        assert PLANS[PlanTier.ENTERPRISE].monthly_request_limit == 100_000

    @pytest.mark.unit
    def test_plan_tier_enum_values_match_keys(self):
        for tier in PLANS:
            assert isinstance(tier, PlanTier)


# ===========================================================================
# InMemoryCacheAdapter
# ===========================================================================


class TestInMemoryCacheAdapter:
    @pytest.mark.unit
    async def test_set_and_get(self):
        c = InMemoryCacheAdapter()
        await c.set("k", "v")
        assert await c.get("k") == "v"

    @pytest.mark.unit
    async def test_get_missing_key_returns_none(self):
        c = InMemoryCacheAdapter()
        assert await c.get("missing") is None

    @pytest.mark.unit
    async def test_delete_removes_key(self):
        c = InMemoryCacheAdapter()
        await c.set("k", "v")
        await c.delete("k")
        assert await c.get("k") is None

    @pytest.mark.unit
    async def test_delete_wildcard(self):
        c = InMemoryCacheAdapter()
        await c.set("events:user1:a", 1)
        await c.set("events:user1:b", 2)
        await c.set("other:key", 3)
        await c.delete("events:user1:*")
        assert await c.get("events:user1:a") is None
        assert await c.get("events:user1:b") is None
        assert await c.get("other:key") == 3  # untouched

    @pytest.mark.unit
    async def test_exists_true_for_set_key(self):
        c = InMemoryCacheAdapter()
        await c.set("k", "v")
        assert await c.exists("k") is True

    @pytest.mark.unit
    async def test_exists_false_for_missing_key(self):
        c = InMemoryCacheAdapter()
        assert await c.exists("no-such-key") is False

    @pytest.mark.unit
    async def test_increment_starts_at_zero(self):
        c = InMemoryCacheAdapter()
        result = await c.increment("counter")
        assert result == 1

    @pytest.mark.unit
    async def test_increment_accumulates(self):
        c = InMemoryCacheAdapter()
        await c.increment("counter", 5)
        await c.increment("counter", 3)
        assert await c.get("counter") == 8

    @pytest.mark.unit
    async def test_get_or_set_stores_and_returns(self):
        c = InMemoryCacheAdapter()
        val = await c.get_or_set("k", lambda: "computed")
        assert val == "computed"
        assert await c.get("k") == "computed"

    @pytest.mark.unit
    async def test_get_or_set_returns_cached_if_present(self):
        c = InMemoryCacheAdapter()
        await c.set("k", "existing")
        val = await c.get_or_set("k", lambda: "should-not-be-called")
        assert val == "existing"

    @pytest.mark.unit
    async def test_overwrite_with_set(self):
        c = InMemoryCacheAdapter()
        await c.set("k", "v1")
        await c.set("k", "v2")
        assert await c.get("k") == "v2"

    @pytest.mark.unit
    async def test_set_stores_dict_value(self):
        c = InMemoryCacheAdapter()
        obj = {"a": 1, "b": [1, 2, 3]}
        await c.set("obj", obj)
        assert await c.get("obj") == obj


# ===========================================================================
# Domain Events
# ===========================================================================


class TestDomainEvents:
    @pytest.mark.unit
    def test_domain_event_has_event_id(self):
        ev = DomainEvent()
        assert isinstance(ev.event_id, uuid.UUID)

    @pytest.mark.unit
    def test_domain_event_has_occurred_at(self):
        ev = DomainEvent()
        assert isinstance(ev.occurred_at, datetime)

    @pytest.mark.unit
    def test_each_domain_event_has_unique_id(self):
        e1 = DomainEvent()
        e2 = DomainEvent()
        assert e1.event_id != e2.event_id

    @pytest.mark.unit
    def test_event_created_defaults(self):
        uid = uuid.uuid4()
        ev = EventCreated(user_id=uid, title="Meeting")
        assert ev.user_id == uid
        assert ev.title == "Meeting"
        assert isinstance(ev.calendar_event_id, uuid.UUID)

    @pytest.mark.unit
    def test_event_updated_has_changes_tuple(self):
        ev = EventUpdated(changes=("title", "location"))
        assert "title" in ev.changes

    @pytest.mark.unit
    def test_event_deleted_fields(self):
        uid = uuid.uuid4()
        eid = uuid.uuid4()
        ev = EventDeleted(user_id=uid, calendar_event_id=eid)
        assert ev.user_id == uid
        assert ev.calendar_event_id == eid

    @pytest.mark.unit
    def test_event_conflict_detected_has_two_event_ids(self):
        ev = EventConflictDetected()
        assert isinstance(ev.event_a_id, uuid.UUID)
        assert isinstance(ev.event_b_id, uuid.UUID)

    @pytest.mark.unit
    def test_user_quota_exceeded_fields(self):
        uid = uuid.uuid4()
        ev = UserQuotaExceeded(user_id=uid, plan="free", current_usage=51, limit=50)
        assert ev.user_id == uid
        assert ev.current_usage == 51
        assert ev.limit == 50

    @pytest.mark.unit
    def test_conversation_started_fields(self):
        uid = uuid.uuid4()
        ev = ConversationStarted(user_id=uid)
        assert ev.user_id == uid
        assert isinstance(ev.conversation_id, uuid.UUID)

    @pytest.mark.unit
    def test_domain_events_are_frozen(self):
        ev = DomainEvent()
        with pytest.raises((AttributeError, TypeError)):
            ev.event_id = uuid.uuid4()  # type: ignore[misc]


# ===========================================================================
# RedisUsageTracker (backed by InMemoryCache in unit tests)
# ===========================================================================


class TestRedisUsageTracker:
    def _cache_and_tracker(self) -> tuple[InMemoryCacheAdapter, RedisUsageTracker]:
        cache = InMemoryCacheAdapter()
        tracker = RedisUsageTracker(cache)
        return cache, tracker

    @pytest.mark.unit
    async def test_initial_request_count_is_zero(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        count = await tracker.get_monthly_request_count(uid)
        assert count == 0

    @pytest.mark.unit
    async def test_record_request_increments_count(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        await tracker.record_request(uid)
        assert await tracker.get_monthly_request_count(uid) == 1

    @pytest.mark.unit
    async def test_record_request_multiple_times(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        for _ in range(5):
            await tracker.record_request(uid)
        assert await tracker.get_monthly_request_count(uid) == 5

    @pytest.mark.unit
    async def test_different_users_have_separate_counts(self):
        _, tracker = self._cache_and_tracker()
        uid1, uid2 = uuid.uuid4(), uuid.uuid4()
        await tracker.record_request(uid1)
        await tracker.record_request(uid1)
        await tracker.record_request(uid2)
        assert await tracker.get_monthly_request_count(uid1) == 2
        assert await tracker.get_monthly_request_count(uid2) == 1

    @pytest.mark.unit
    async def test_is_within_quota_when_below_limit(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        await tracker.record_request(uid)
        assert await tracker.is_within_quota(uid, limit=10) is True

    @pytest.mark.unit
    async def test_is_not_within_quota_when_at_limit(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        for _ in range(10):
            await tracker.record_request(uid)
        assert await tracker.is_within_quota(uid, limit=10) is False

    @pytest.mark.unit
    async def test_record_request_with_token_usage(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        tu = TokenUsage(prompt_tokens=100, completion_tokens=50, model="gpt-4o-mini")
        await tracker.record_request(uid, token_usage=tu)
        tokens = await tracker.get_monthly_token_usage(uid)
        assert tokens == 150

    @pytest.mark.unit
    async def test_initial_token_usage_is_zero(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        assert await tracker.get_monthly_token_usage(uid) == 0

    @pytest.mark.unit
    async def test_get_monthly_cost_estimate_starts_at_zero(self):
        _, tracker = self._cache_and_tracker()
        uid = uuid.uuid4()
        assert await tracker.get_monthly_cost_estimate(uid) == 0.0
