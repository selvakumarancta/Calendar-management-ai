"""
Unit tests for CalendarService.

All external ports (provider, repository, cache) are replaced with simple
async stubs so no DB or network is needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.application.dto import CreateEventDTO, DateRangeDTO, UpdateEventDTO
from src.application.services.calendar_service import CalendarService
from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.exceptions import (
    EventConflictError,
    EventInPastError,
    EventNotFoundError,
    InvalidTimeRangeError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_NOW = datetime.now(timezone.utc)


def _future(hours: int = 1) -> datetime:
    return _NOW + timedelta(hours=hours)


def _event(
    title: str = "Meeting", start_offset_h: int = 1, dur_h: int = 1
) -> CalendarEvent:
    start = _future(start_offset_h)
    return CalendarEvent(
        id=uuid.uuid4(),
        user_id=_USER_ID,
        title=title,
        start_time=start,
        end_time=start + timedelta(hours=dur_h),
        status=EventStatus.CONFIRMED,
    )


def _make_svc(
    existing_events: list[CalendarEvent] | None = None,
    provider_event: CalendarEvent | None = None,
) -> CalendarService:
    """Build a CalendarService backed by async mocks."""
    provider = AsyncMock()
    provider.list_events.return_value = existing_events or []
    provider.create_event.side_effect = lambda user_id, ev: ev
    provider.update_event.side_effect = lambda user_id, ev: ev
    provider.delete_event.return_value = None
    provider.get_event.return_value = provider_event

    repo = AsyncMock()
    repo.create.return_value = None
    repo.update.return_value = None
    repo.delete.return_value = None

    cache = AsyncMock()
    cache.delete.return_value = None
    cache.get.return_value = None  # no cached data by default
    cache.set.return_value = None

    return CalendarService(provider, repo, cache)


def _dto(start_offset_h: int = 2, dur_h: int = 1, **kwargs) -> CreateEventDTO:
    start = _future(start_offset_h)
    return CreateEventDTO(
        title=kwargs.pop("title", "Team Sync"),
        start_time=start,
        end_time=start + timedelta(hours=dur_h),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


class TestCreateEvent:
    @pytest.mark.unit
    async def test_returns_event_response(self):
        svc = _make_svc()
        result = await svc.create_event(_USER_ID, _dto())
        assert result.title == "Team Sync"

    @pytest.mark.unit
    async def test_persists_to_repo(self):
        provider = AsyncMock()
        provider.list_events.return_value = []
        provider.create_event.side_effect = lambda uid, ev: ev
        repo = AsyncMock()
        cache = AsyncMock()
        svc = CalendarService(provider, repo, cache)
        await svc.create_event(_USER_ID, _dto())
        repo.create.assert_awaited_once()

    @pytest.mark.unit
    async def test_invalidates_cache(self):
        provider = AsyncMock()
        provider.list_events.return_value = []
        provider.create_event.side_effect = lambda uid, ev: ev
        repo = AsyncMock()
        cache = AsyncMock()
        svc = CalendarService(provider, repo, cache)
        await svc.create_event(_USER_ID, _dto())
        cache.delete.assert_awaited_once()

    @pytest.mark.unit
    async def test_raises_invalid_time_range_when_end_before_start(self):
        svc = _make_svc()
        start = _future(2)
        dto = CreateEventDTO(
            title="Bad Event",
            start_time=start,
            end_time=start - timedelta(minutes=1),
        )
        with pytest.raises(InvalidTimeRangeError):
            await svc.create_event(_USER_ID, dto)

    @pytest.mark.unit
    async def test_raises_event_in_past(self):
        svc = _make_svc()
        past = _NOW - timedelta(hours=1)
        dto = CreateEventDTO(
            title="Past Event",
            start_time=past,
            end_time=past + timedelta(hours=1),
        )
        with pytest.raises(EventInPastError):
            await svc.create_event(_USER_ID, dto)

    @pytest.mark.unit
    async def test_grace_period_allows_events_within_10_min_of_now(self):
        """Events up to 10 min in the past should NOT raise EventInPastError."""
        svc = _make_svc()
        slightly_past = _NOW - timedelta(minutes=5)
        dto = CreateEventDTO(
            title="Recent Event",
            start_time=slightly_past,
            end_time=slightly_past + timedelta(hours=1),
        )
        # Should not raise — within grace period
        result = await svc.create_event(_USER_ID, dto)
        assert result.title == "Recent Event"

    @pytest.mark.unit
    async def test_raises_conflict_error_on_overlap(self):
        existing = _event("Existing Meeting", start_offset_h=2, dur_h=1)
        svc = _make_svc(existing_events=[existing])
        # New event fully overlaps with existing
        dto = CreateEventDTO(
            title="Overlapping",
            start_time=_future(2),
            end_time=_future(3),
        )
        with pytest.raises(EventConflictError):
            await svc.create_event(_USER_ID, dto)

    @pytest.mark.unit
    async def test_no_conflict_when_events_are_adjacent(self):
        """Adjacent events (end == start of next) should not conflict."""
        existing = _event("Morning Standup", start_offset_h=2, dur_h=1)
        svc = _make_svc(existing_events=[existing])
        # Starts exactly when existing ends → no overlap
        start = _future(3)
        dto = CreateEventDTO(
            title="Next Event",
            start_time=start,
            end_time=start + timedelta(hours=1),
        )
        result = await svc.create_event(_USER_ID, dto)
        assert result.title == "Next Event"

    @pytest.mark.unit
    async def test_all_day_event_bypasses_time_range_check(self):
        svc = _make_svc()
        start = _future(2)
        dto = CreateEventDTO(
            title="Company Holiday",
            start_time=start,
            end_time=start,  # same time is fine for all-day
            is_all_day=True,
        )
        result = await svc.create_event(_USER_ID, dto)
        assert result.title == "Company Holiday"

    @pytest.mark.unit
    async def test_attendee_emails_stored_as_attendees(self):
        svc = _make_svc()
        dto = _dto(attendee_emails=["alice@example.com", "bob@example.com"])
        result = await svc.create_event(_USER_ID, dto)
        assert result.title == "Team Sync"


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


class TestUpdateEvent:
    @pytest.mark.unit
    async def test_raises_not_found_when_event_missing(self):
        svc = _make_svc(provider_event=None)
        dto = UpdateEventDTO(event_id=str(uuid.uuid4()), title="New Title")
        with pytest.raises(EventNotFoundError):
            await svc.update_event(_USER_ID, dto)

    @pytest.mark.unit
    async def test_updates_title(self):
        ev = _event("Old Title")
        svc = _make_svc(provider_event=ev)
        dto = UpdateEventDTO(event_id=str(ev.id), title="New Title")
        result = await svc.update_event(_USER_ID, dto)
        assert result.title == "New Title"

    @pytest.mark.unit
    async def test_updates_location(self):
        ev = _event()
        svc = _make_svc(provider_event=ev)
        dto = UpdateEventDTO(event_id=str(ev.id), location="Zoom")
        result = await svc.update_event(_USER_ID, dto)
        assert result.location == "Zoom"

    @pytest.mark.unit
    async def test_updates_description(self):
        ev = _event()
        svc = _make_svc(provider_event=ev)
        dto = UpdateEventDTO(event_id=str(ev.id), description="Agenda: Q3 review")
        result = await svc.update_event(_USER_ID, dto)
        assert result.description == "Agenda: Q3 review"

    @pytest.mark.unit
    async def test_partial_update_leaves_other_fields_unchanged(self):
        ev = _event("My Meeting")
        ev.location = "Office"
        svc = _make_svc(provider_event=ev)
        dto = UpdateEventDTO(event_id=str(ev.id), description="Notes here")
        result = await svc.update_event(_USER_ID, dto)
        assert result.title == "My Meeting"

    @pytest.mark.unit
    async def test_update_persists_to_provider(self):
        ev = _event()
        provider = AsyncMock()
        provider.get_event.return_value = ev
        provider.update_event.side_effect = lambda uid, e: e
        repo = AsyncMock()
        cache = AsyncMock()
        svc = CalendarService(provider, repo, cache)
        dto = UpdateEventDTO(event_id=str(ev.id), title="Updated")
        await svc.update_event(_USER_ID, dto)
        provider.update_event.assert_awaited_once()


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


class TestListEvents:
    @pytest.mark.unit
    async def test_returns_empty_list_when_no_events(self):
        svc = _make_svc(existing_events=[])
        dto = DateRangeDTO(start=_future(0), end=_future(24))
        result = await svc.list_events(_USER_ID, dto)
        assert result == []

    @pytest.mark.unit
    async def test_returns_mapped_events(self):
        ev1 = _event("Event A", 1, 1)
        ev2 = _event("Event B", 3, 1)
        svc = _make_svc(existing_events=[ev1, ev2])
        dto = DateRangeDTO(start=_future(0), end=_future(24))
        result = await svc.list_events(_USER_ID, dto)
        titles = [r.title for r in result]
        assert "Event A" in titles
        assert "Event B" in titles

    @pytest.mark.unit
    async def test_returns_cached_result(self):
        """When cache has data, return it without hitting provider."""
        cached_data = [{"id": "cached"}]
        svc = _make_svc()
        svc._cache.get.return_value = cached_data
        dto = DateRangeDTO(start=_future(0), end=_future(24))
        result = await svc.list_events(_USER_ID, dto)
        assert result == cached_data
        svc._provider.list_events.assert_not_awaited()


# ---------------------------------------------------------------------------
# find_free_slots
# ---------------------------------------------------------------------------


class TestFindFreeSlots:
    @pytest.mark.unit
    async def test_returns_list(self):
        from unittest.mock import AsyncMock, MagicMock

        provider = AsyncMock()
        slot = MagicMock()
        slot.start = _future(1)
        slot.end = _future(2)
        slot.duration_minutes = 60
        provider.find_free_slots.return_value = [slot]
        repo = AsyncMock()
        cache = AsyncMock()
        cache.get.return_value = None
        cache.set.return_value = None
        svc = CalendarService(provider, repo, cache)
        result = await svc.find_free_slots(_USER_ID, _future(0), _future(8))
        assert len(result) == 1
        assert result[0].duration_minutes == 60

    @pytest.mark.unit
    async def test_returns_empty_when_no_slots(self):
        svc = _make_svc()
        svc._provider.find_free_slots.return_value = []
        result = await svc.find_free_slots(_USER_ID, _future(0), _future(8))
        assert result == []


# ---------------------------------------------------------------------------
# check_conflicts
# ---------------------------------------------------------------------------


class TestCheckConflicts:
    @pytest.mark.unit
    async def test_returns_conflicting_events(self):
        start = _future(1)
        end = _future(3)
        ev = CalendarEvent(
            id=uuid.uuid4(),
            user_id=_USER_ID,
            title="Conflict",
            start_time=_future(2),
            end_time=_future(4),
            status=EventStatus.CONFIRMED,
        )
        svc = _make_svc(existing_events=[ev])
        result = await svc.check_conflicts(_USER_ID, start, end)
        assert len(result) == 1

    @pytest.mark.unit
    async def test_returns_empty_when_no_conflicts(self):
        # Event is after proposed window
        ev = _event("Later", start_offset_h=5, dur_h=1)
        svc = _make_svc(existing_events=[ev])
        result = await svc.check_conflicts(_USER_ID, _future(1), _future(2))
        assert result == []


# ---------------------------------------------------------------------------
# Additional create_event edge cases (line 53 — naive datetime branch)
# ---------------------------------------------------------------------------


class TestCreateEventNaiveDatetime:
    @pytest.mark.unit
    async def test_naive_datetime_is_treated_as_utc(self):
        """Passing naive datetimes triggers the tzinfo-is-None branch (line 53)."""
        svc = _make_svc()
        # Naive datetimes 2 hours in the future
        base = datetime.utcnow() + timedelta(hours=2)
        dto = CreateEventDTO(
            title="Naive Test",
            start_time=base,
            end_time=base + timedelta(hours=1),
        )
        result = await svc.create_event(_USER_ID, dto)
        assert result.title == "Naive Test"


# ---------------------------------------------------------------------------
# update_event edge cases (lines 122, 124 — location + reschedule)
# ---------------------------------------------------------------------------


class TestUpdateEventEdgeCases:
    @pytest.mark.unit
    async def test_update_location(self):
        """Updating only the location hits line 122."""
        existing = _event("Meeting")
        svc = _make_svc(provider_event=existing)
        dto = UpdateEventDTO(event_id=str(existing.id), location="Room 4")
        result = await svc.update_event(_USER_ID, dto)
        assert result is not None

    @pytest.mark.unit
    async def test_update_reschedule(self):
        """Updating start_time + end_time hits line 124 (event.reschedule)."""
        existing = _event("Meeting")
        svc = _make_svc(provider_event=existing)
        new_start = _future(3)
        new_end = _future(4)
        dto = UpdateEventDTO(
            event_id=str(existing.id),
            start_time=new_start,
            end_time=new_end,
        )
        result = await svc.update_event(_USER_ID, dto)
        assert result is not None


# ---------------------------------------------------------------------------
# delete_event (lines 134-137 — success branch)
# ---------------------------------------------------------------------------


class TestDeleteEventSuccess:
    @pytest.mark.unit
    async def test_delete_event_returns_true_and_clears_cache(self):
        """delete_event with success=True invalidates cache (lines 134-137)."""
        svc = _make_svc()
        svc._provider.delete_event.return_value = True
        result = await svc.delete_event(_USER_ID, "evt-123")
        assert result is True
        svc._cache.delete.assert_awaited()
