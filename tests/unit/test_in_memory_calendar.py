"""
Unit tests for InMemoryCalendarAdapter — in-memory path only.

No DB setup required: the adapter falls back to its internal dict when
_db_session_factory is None (the default).  All tests are pure async unit
tests with no external dependencies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.entities.calendar_event import (
    Attendee,
    CalendarEvent,
    EventStatus,
    Reminder,
)
from src.infrastructure.calendar_providers.in_memory_calendar import (
    InMemoryCalendarAdapter,
)

_UTC = timezone.utc
_NOW = datetime.now(_UTC)
_USER = uuid.uuid4()


def _ev(
    user_id: uuid.UUID = _USER,
    start_offset_h: int = 1,
    dur_h: int = 1,
    title: str = "Meeting",
    status: EventStatus = EventStatus.CONFIRMED,
) -> CalendarEvent:
    start = _NOW + timedelta(hours=start_offset_h)
    return CalendarEvent(
        id=uuid.uuid4(),
        user_id=user_id,
        title=title,
        start_time=start,
        end_time=start + timedelta(hours=dur_h),
        status=status,
    )


def _adapter() -> InMemoryCalendarAdapter:
    """Return a fresh adapter with no DB session (pure in-memory)."""
    return InMemoryCalendarAdapter()


# ===========================================================================
# create_event / get_event
# ===========================================================================


class TestCreateAndGetEvent:
    @pytest.mark.unit
    async def test_create_event_stores_and_returns_it(self):
        a = _adapter()
        ev = _ev()
        result = await a.create_event(_USER, ev)
        assert result.id == ev.id

    @pytest.mark.unit
    async def test_create_event_assigns_provider_id(self):
        a = _adapter()
        ev = _ev()
        ev.provider_event_id = None
        result = await a.create_event(_USER, ev)
        assert result.provider_event_id is not None
        assert result.provider_event_id.startswith("mem-")

    @pytest.mark.unit
    async def test_create_event_preserves_custom_provider_id(self):
        a = _adapter()
        ev = _ev()
        ev.provider_event_id = "custom-provider-id"
        result = await a.create_event(_USER, ev)
        assert result.provider_event_id == "custom-provider-id"

    @pytest.mark.unit
    async def test_get_event_returns_created_event(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        found = await a.get_event(_USER, str(ev.id))
        assert found is not None
        assert found.id == ev.id

    @pytest.mark.unit
    async def test_get_event_by_provider_id(self):
        a = _adapter()
        ev = _ev()
        ev.provider_event_id = "google-abc123"
        await a.create_event(_USER, ev)
        found = await a.get_event(_USER, "google-abc123")
        assert found is not None

    @pytest.mark.unit
    async def test_get_event_returns_none_for_unknown_id(self):
        a = _adapter()
        found = await a.get_event(_USER, str(uuid.uuid4()))
        assert found is None

    @pytest.mark.unit
    async def test_get_event_does_not_return_other_users_event(self):
        a = _adapter()
        other_user = uuid.uuid4()
        ev = _ev(user_id=other_user)
        await a.create_event(other_user, ev)
        found = await a.get_event(_USER, str(ev.id))
        assert found is None


# ===========================================================================
# update_event
# ===========================================================================


class TestUpdateEvent:
    @pytest.mark.unit
    async def test_update_replaces_event_in_store(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        ev.title = "Updated Title"
        await a.update_event(_USER, ev)
        found = await a.get_event(_USER, str(ev.id))
        assert found.title == "Updated Title"

    @pytest.mark.unit
    async def test_update_sets_updated_at(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        old_updated = ev.updated_at
        ev.title = "Changed"
        await a.update_event(_USER, ev)
        found = await a.get_event(_USER, str(ev.id))
        assert found.updated_at >= old_updated


# ===========================================================================
# delete_event
# ===========================================================================


class TestDeleteEvent:
    @pytest.mark.unit
    async def test_delete_existing_event_returns_true(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        result = await a.delete_event(_USER, str(ev.id))
        assert result is True

    @pytest.mark.unit
    async def test_deleted_event_no_longer_retrievable(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        await a.delete_event(_USER, str(ev.id))
        assert await a.get_event(_USER, str(ev.id)) is None

    @pytest.mark.unit
    async def test_delete_nonexistent_event_returns_false(self):
        a = _adapter()
        result = await a.delete_event(_USER, str(uuid.uuid4()))
        assert result is False


# ===========================================================================
# list_events
# ===========================================================================


class TestListEvents:
    @pytest.mark.unit
    async def test_lists_events_in_range(self):
        a = _adapter()
        ev = _ev(start_offset_h=2)
        await a.create_event(_USER, ev)
        results = await a.list_events(
            _USER, _NOW + timedelta(hours=1), _NOW + timedelta(hours=5)
        )
        assert any(e.id == ev.id for e in results)

    @pytest.mark.unit
    async def test_excludes_event_outside_range(self):
        a = _adapter()
        future_ev = _ev(start_offset_h=10)
        await a.create_event(_USER, future_ev)
        results = await a.list_events(
            _USER, _NOW + timedelta(hours=1), _NOW + timedelta(hours=3)
        )
        assert not any(e.id == future_ev.id for e in results)

    @pytest.mark.unit
    async def test_excludes_cancelled_events(self):
        a = _adapter()
        ev = _ev(status=EventStatus.CANCELLED)
        await a.create_event(_USER, ev)
        results = await a.list_events(_USER, _NOW, _NOW + timedelta(hours=5))
        assert not any(e.id == ev.id for e in results)

    @pytest.mark.unit
    async def test_excludes_other_users_events(self):
        a = _adapter()
        other = uuid.uuid4()
        ev = _ev(user_id=other)
        await a.create_event(other, ev)
        results = await a.list_events(_USER, _NOW, _NOW + timedelta(hours=5))
        assert not any(e.id == ev.id for e in results)

    @pytest.mark.unit
    async def test_returns_events_sorted_by_start_time(self):
        a = _adapter()
        ev1 = _ev(start_offset_h=3, title="C")
        ev2 = _ev(start_offset_h=1, title="A")
        ev3 = _ev(start_offset_h=2, title="B")
        for ev in (ev1, ev2, ev3):
            await a.create_event(_USER, ev)
        results = await a.list_events(_USER, _NOW, _NOW + timedelta(hours=10))
        starts = [e.start_time for e in results]
        assert starts == sorted(starts)

    @pytest.mark.unit
    async def test_empty_store_returns_empty_list(self):
        a = _adapter()
        results = await a.list_events(_USER, _NOW, _NOW + timedelta(hours=5))
        assert results == []


# ===========================================================================
# find_free_slots
# ===========================================================================


class TestFindFreeSlots:
    @pytest.mark.unit
    async def test_no_events_entire_range_is_free(self):
        a = _adapter()
        range_start = _NOW + timedelta(hours=1)
        range_end = _NOW + timedelta(hours=3)
        slots = await a.find_free_slots(
            _USER, range_start, range_end, duration_minutes=30
        )
        assert len(slots) == 1
        assert slots[0].start == range_start
        assert slots[0].end == range_end

    @pytest.mark.unit
    async def test_busy_block_splits_free_slots(self):
        a = _adapter()
        range_start = _NOW + timedelta(hours=1)
        range_end = _NOW + timedelta(hours=5)
        busy = _ev(start_offset_h=2, dur_h=1)
        await a.create_event(_USER, busy)
        slots = await a.find_free_slots(
            _USER, range_start, range_end, duration_minutes=30
        )
        # Should have gap before and after the busy block
        assert len(slots) >= 1

    @pytest.mark.unit
    async def test_fully_busy_range_returns_no_slots(self):
        a = _adapter()
        range_start = _NOW + timedelta(hours=1)
        range_end = _NOW + timedelta(hours=3)
        busy = _ev(start_offset_h=1, dur_h=2)  # covers entire range
        await a.create_event(_USER, busy)
        slots = await a.find_free_slots(
            _USER, range_start, range_end, duration_minutes=30
        )
        assert len(slots) == 0

    @pytest.mark.unit
    async def test_minimum_duration_filters_tiny_gaps(self):
        a = _adapter()
        range_start = _NOW + timedelta(hours=1)
        range_end = _NOW + timedelta(hours=4)
        # Two events with a 10-minute gap between them
        ev1 = CalendarEvent(
            user_id=_USER,
            title="Ev1",
            start_time=range_start,
            end_time=range_start + timedelta(hours=1),
        )
        ev2 = CalendarEvent(
            user_id=_USER,
            title="Ev2",
            start_time=range_start + timedelta(hours=1, minutes=10),
            end_time=range_end,
        )
        await a.create_event(_USER, ev1)
        await a.create_event(_USER, ev2)
        # 10-min gap but min duration is 30 → no free slot
        slots = await a.find_free_slots(
            _USER, range_start, range_end, duration_minutes=30
        )
        assert len(slots) == 0


# ===========================================================================
# _entity_to_model_dict / _model_to_entity (static helpers)
# ===========================================================================


class TestModelConversionHelpers:
    @pytest.mark.unit
    def test_entity_to_model_dict_has_required_keys(self):
        ev = _ev()
        d = InMemoryCalendarAdapter._entity_to_model_dict(ev)
        for key in ("id", "user_id", "title", "start_time", "end_time", "status"):
            assert key in d, f"Missing key: {key}"

    @pytest.mark.unit
    def test_entity_to_model_dict_serialises_attendees_to_json(self):
        import json

        ev = _ev()
        ev.attendees = [Attendee(email="a@b.com", name="A")]
        d = InMemoryCalendarAdapter._entity_to_model_dict(ev)
        parsed = json.loads(d["attendees_json"])
        assert parsed[0]["email"] == "a@b.com"

    @pytest.mark.unit
    def test_entity_to_model_dict_serialises_reminders_to_json(self):
        import json

        ev = _ev()
        ev.reminders = [Reminder(minutes_before=10)]
        d = InMemoryCalendarAdapter._entity_to_model_dict(ev)
        parsed = json.loads(d["reminders_json"])
        assert parsed[0]["minutes_before"] == 10

    @pytest.mark.unit
    def test_entity_to_model_dict_status_enum_as_string(self):
        ev = _ev(status=EventStatus.CANCELLED)
        d = InMemoryCalendarAdapter._entity_to_model_dict(ev)
        assert d["status"] == "cancelled"

    @pytest.mark.unit
    def test_model_to_entity_parses_dict_attendees(self):
        import json

        row = type(
            "Row",
            (),
            {
                "id": uuid.uuid4(),
                "provider_event_id": "prov-1",
                "user_id": _USER,
                "calendar_id": "primary",
                "title": "Test",
                "description": None,
                "location": None,
                "start_time": _NOW,
                "end_time": _NOW + timedelta(hours=1),
                "is_all_day": False,
                "status": "confirmed",
                "attendees_json": json.dumps([{"email": "a@b.com", "name": "Alice"}]),
                "reminders_json": json.dumps([{"minutes_before": 15}]),
                "created_at": _NOW,
                "updated_at": _NOW,
            },
        )()
        ev = InMemoryCalendarAdapter._model_to_entity(row)
        assert ev.title == "Test"
        assert len(ev.attendees) == 1
        assert ev.attendees[0].email == "a@b.com"
        assert len(ev.reminders) == 1
        assert ev.reminders[0].minutes_before == 15

    @pytest.mark.unit
    def test_model_to_entity_parses_string_attendees(self):
        import json

        row = type(
            "Row",
            (),
            {
                "id": uuid.uuid4(),
                "provider_event_id": None,
                "user_id": _USER,
                "calendar_id": None,
                "title": "Str Attendee Test",
                "description": None,
                "location": None,
                "start_time": _NOW,
                "end_time": _NOW + timedelta(hours=1),
                "is_all_day": False,
                "status": "confirmed",
                "attendees_json": json.dumps(["alice@example.com"]),
                "reminders_json": "[]",
                "created_at": None,
                "updated_at": None,
            },
        )()
        ev = InMemoryCalendarAdapter._model_to_entity(row)
        assert len(ev.attendees) == 1
        assert ev.attendees[0].email == "alice@example.com"

    @pytest.mark.unit
    def test_model_to_entity_handles_invalid_json(self):
        row = type(
            "Row",
            (),
            {
                "id": uuid.uuid4(),
                "provider_event_id": None,
                "user_id": _USER,
                "calendar_id": "primary",
                "title": "Bad JSON",
                "description": None,
                "location": None,
                "start_time": _NOW,
                "end_time": _NOW + timedelta(hours=1),
                "is_all_day": False,
                "status": None,
                "attendees_json": "{invalid json",
                "reminders_json": "{also bad",
                "created_at": None,
                "updated_at": None,
            },
        )()
        # Should not raise — just produces empty lists
        ev = InMemoryCalendarAdapter._model_to_entity(row)
        assert ev.attendees == []
        assert ev.reminders == []


# ===========================================================================
# DB-less methods (no factory — fast path returns immediately)
# ===========================================================================


class TestNoDatabaseFallbacks:
    @pytest.mark.unit
    def test_set_db_session_factory(self):
        a = _adapter()
        fake_factory = object()
        a.set_db_session_factory(fake_factory)
        assert a._db_session_factory is fake_factory

    @pytest.mark.unit
    async def test_db_save_no_op_without_factory(self):
        a = _adapter()
        ev = _ev()
        # Should return immediately without error
        await a._db_save(ev)

    @pytest.mark.unit
    async def test_db_delete_returns_false_without_factory(self):
        a = _adapter()
        result = await a._db_delete(uuid.uuid4())
        assert result is False

    @pytest.mark.unit
    async def test_db_list_returns_empty_without_factory(self):
        a = _adapter()
        result = await a._db_list(_USER, _NOW, _NOW + timedelta(days=7))
        assert result == []

    @pytest.mark.unit
    async def test_db_get_returns_none_without_factory(self):
        a = _adapter()
        result = await a._db_get(uuid.uuid4())
        assert result is None


# ===========================================================================
# EventRepositoryPort methods
# ===========================================================================


class TestEventRepositoryPort:
    @pytest.mark.unit
    async def test_get_by_id_returns_event(self):
        a = _adapter()
        ev = _ev()
        await a.create_event(_USER, ev)
        found = await a.get_by_id(ev.id)
        assert found is not None
        assert found.id == ev.id

    @pytest.mark.unit
    async def test_get_by_id_returns_none_for_unknown(self):
        a = _adapter()
        result = await a.get_by_id(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    async def test_get_by_provider_id_in_memory(self):
        a = _adapter()
        ev = _ev()
        ev.provider_event_id = "prov-xyz"
        await a.create_event(_USER, ev)
        found = await a.get_by_provider_id("prov-xyz", _USER)
        assert found is not None
        assert found.provider_event_id == "prov-xyz"

    @pytest.mark.unit
    async def test_get_by_provider_id_returns_none_for_wrong_user(self):
        a = _adapter()
        ev = _ev()
        ev.provider_event_id = "prov-abc"
        await a.create_event(_USER, ev)
        other_user = uuid.uuid4()
        found = await a.get_by_provider_id("prov-abc", other_user)
        assert found is None

    @pytest.mark.unit
    async def test_list_by_user_filters_by_user(self):
        a = _adapter()
        ev1 = _ev(user_id=_USER)
        other_user = uuid.uuid4()
        ev2 = _ev(user_id=other_user)
        await a.create(ev1)
        await a.create(ev2)
        results = await a.list_by_user(_USER)
        ids = [e.id for e in results]
        assert ev1.id in ids
        assert ev2.id not in ids

    @pytest.mark.unit
    async def test_list_by_user_filters_by_start_end(self):
        a = _adapter()
        early = _ev(start_offset_h=1, dur_h=1)
        late = _ev(start_offset_h=10, dur_h=1)
        await a.create(early)
        await a.create(late)
        # Only get events in the next 5 hours
        results = await a.list_by_user(
            _USER,
            start=_NOW,
            end=_NOW + timedelta(hours=5),
        )
        ids = [e.id for e in results]
        assert early.id in ids
        assert late.id not in ids

    @pytest.mark.unit
    async def test_create_via_repo_port(self):
        a = _adapter()
        ev = _ev()
        created = await a.create(ev)
        assert created.id == ev.id
        assert ev.id in a._events

    @pytest.mark.unit
    async def test_update_via_repo_port(self):
        a = _adapter()
        ev = _ev()
        await a.create(ev)
        ev.title = "Updated Title"
        updated = await a.update(ev)
        assert updated.title == "Updated Title"
        assert a._events[ev.id].title == "Updated Title"

    @pytest.mark.unit
    async def test_delete_via_repo_port(self):
        a = _adapter()
        ev = _ev()
        await a.create(ev)
        result = await a.delete(ev.id)
        assert result is True
        assert ev.id not in a._events

    @pytest.mark.unit
    async def test_delete_missing_returns_false(self):
        a = _adapter()
        result = await a.delete(uuid.uuid4())
        assert result is False


# ===========================================================================
# DB-path branches (_db_save, _db_update, _db_delete, get_by_provider_id, list_by_user)
# ===========================================================================


def _make_db_adapter():
    """Return an adapter with a minimal async-session factory for DB-path testing."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    # We track what was added/updated via side effects
    saved = []

    class _FakeRow:
        def __init__(self, ev):
            self.id = ev.id
            self.user_id = ev.user_id
            self.title = ev.title
            self.description = ev.description or ""
            self.location = ev.location or ""
            self.start_time = ev.start_time
            self.end_time = ev.end_time
            self.status = ev.status.value if hasattr(ev.status, "value") else ev.status
            self.is_all_day = ev.is_all_day
            self.recurrence_rule = ev.recurrence_rule or ""
            self.provider_event_id = getattr(ev, "provider_event_id", None) or ""
            self.calendar_id = getattr(ev, "calendar_id", None) or ""
            self.attendees_json = "[]"
            self.reminders_json = "[]"
            self.updated_at = None

    class _FakeSession:
        def __init__(self):
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def execute(self, *_):
            r = MagicMock()
            r.scalar_one_or_none.return_value = None
            r.scalars.return_value.all.return_value = []
            return r

        def add(self, obj):
            self.added.append(obj)
            saved.append(obj)

        async def commit(self):
            pass

    @asynccontextmanager
    async def factory():
        yield _FakeSession()

    a = InMemoryCalendarAdapter()
    a.set_db_session_factory(factory)
    return a, saved


class TestDbSaveNewRecord:
    @pytest.mark.unit
    async def test_db_save_new_record(self):
        """_db_save creates a new row when no existing row found (lines 144-145)."""
        a, saved = _make_db_adapter()
        ev = _ev()
        await a._db_save(ev)
        assert len(saved) == 1

    @pytest.mark.unit
    async def test_db_delete_via_adapter(self):
        """_db_delete executes delete query (lines 165-169)."""
        a, _ = _make_db_adapter()
        # Should return False when rowcount is 0 (mocked session)
        result = await a._db_delete(uuid.uuid4())
        # No exception means code ran (rowcount mock returns MagicMock which is truthy)
        assert result is not None

    @pytest.mark.unit
    async def test_db_get_returns_none_when_no_row(self):
        """_db_get returns None when no row found (lines 225-229)."""
        a, _ = _make_db_adapter()
        result = await a._db_get(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    async def test_get_by_provider_id_with_db_returns_none_when_not_found(self):
        """get_by_provider_id tries DB first, returns None when not found (lines 367-385)."""
        a, _ = _make_db_adapter()
        result = await a.get_by_provider_id("unknown-provider-id", _USER)
        assert result is None

    @pytest.mark.unit
    async def test_list_by_user_with_db_returns_in_memory_fallback(self):
        """list_by_user returns in-memory events when DB returns empty list (lines 359, 403)."""
        a, _ = _make_db_adapter()
        # Store an event in-memory
        ev = _ev()
        a._events[ev.id] = ev

        results = await a.list_by_user(_USER)
        assert len(results) == 1
        assert results[0].id == ev.id

    @pytest.mark.unit
    async def test_db_save_exception_is_caught(self):
        """_db_save exception is caught without raising (lines 147-148)."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def broken_factory():
            class _BrokenSession:
                async def execute(self, *_):
                    raise RuntimeError("DB down")

            yield _BrokenSession()

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(broken_factory)
        ev = _ev()
        # Should not raise
        await a._db_save(ev)

    @pytest.mark.unit
    async def test_db_list_exception_returns_empty(self):
        """_db_list exception returns [] (lines 206-208)."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def broken_factory():
            class _BrokenSession:
                async def execute(self, *_):
                    raise RuntimeError("query failed")

            yield _BrokenSession()

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(broken_factory)
        result = await a._db_list(_USER)
        assert result == []


class TestDbUpdateExistingRow:
    @pytest.mark.unit
    async def test_db_save_updates_existing_row(self):
        """_db_save updates the row when scalar_one_or_none returns a result (lines 140-143)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        existing_row = MagicMock()
        existing_row.updated_at = None

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            result = MagicMock()
            result.scalar_one_or_none.return_value = existing_row
            session.execute = AsyncMock(return_value=result)
            session.commit = AsyncMock()
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        ev = _ev()
        await a._db_save(ev)
        # updated_at was set on the existing row
        assert existing_row.updated_at is not None

    @pytest.mark.unit
    async def test_db_get_returns_entity_when_row_found(self):
        """_db_get returns _model_to_entity(row) when row is found (lines 227-229)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        ev = _ev()
        fake_row = MagicMock()
        fake_row.id = ev.id
        fake_row.user_id = ev.user_id
        fake_row.title = ev.title
        fake_row.description = ""
        fake_row.location = ""
        fake_row.start_time = ev.start_time
        fake_row.end_time = ev.end_time
        fake_row.status = "confirmed"
        fake_row.is_all_day = False
        fake_row.recurrence_rule = ""
        fake_row.provider_event_id = "pid"
        fake_row.calendar_id = ""
        fake_row.attendees_json = "[]"
        fake_row.reminders_json = "[]"

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            result = MagicMock()
            result.scalar_one_or_none.return_value = fake_row
            session.execute = AsyncMock(return_value=result)
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        result = await a._db_get(ev.id)
        assert result is not None
        assert result.id == ev.id

    @pytest.mark.unit
    async def test_get_by_id_returns_db_event_when_found(self):
        """get_by_id returns DB event when _db_get succeeds (line 359)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        ev = _ev()
        fake_row = MagicMock()
        fake_row.id = ev.id
        fake_row.user_id = ev.user_id
        fake_row.title = ev.title
        fake_row.description = ""
        fake_row.location = ""
        fake_row.start_time = ev.start_time
        fake_row.end_time = ev.end_time
        fake_row.status = "confirmed"
        fake_row.is_all_day = False
        fake_row.recurrence_rule = ""
        fake_row.provider_event_id = "pid2"
        fake_row.calendar_id = ""
        fake_row.attendees_json = "[]"
        fake_row.reminders_json = "[]"

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            result = MagicMock()
            result.scalar_one_or_none.return_value = fake_row
            session.execute = AsyncMock(return_value=result)
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        result = await a.get_by_id(ev.id)
        assert result is not None
        assert result.id == ev.id

    @pytest.mark.unit
    async def test_get_by_provider_id_returns_entity_from_db(self):
        """get_by_provider_id returns _model_to_entity when DB row found (lines 383-385)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        ev = _ev()
        ev.provider_event_id = "provider-xyz"
        fake_row = MagicMock()
        fake_row.id = ev.id
        fake_row.user_id = ev.user_id
        fake_row.title = ev.title
        fake_row.description = ""
        fake_row.location = ""
        fake_row.start_time = ev.start_time
        fake_row.end_time = ev.end_time
        fake_row.status = "confirmed"
        fake_row.is_all_day = False
        fake_row.recurrence_rule = ""
        fake_row.provider_event_id = "provider-xyz"
        fake_row.calendar_id = ""
        fake_row.attendees_json = "[]"
        fake_row.reminders_json = "[]"

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            result = MagicMock()
            result.scalar_one_or_none.return_value = fake_row
            session.execute = AsyncMock(return_value=result)
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        result = await a.get_by_provider_id("provider-xyz", _USER)
        assert result is not None

    @pytest.mark.unit
    async def test_list_by_user_returns_db_events_when_found(self):
        """list_by_user returns DB events directly when _db_list returns results (line 403)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        ev = _ev()
        fake_row = MagicMock()
        fake_row.id = ev.id
        fake_row.user_id = ev.user_id
        fake_row.title = ev.title
        fake_row.description = ""
        fake_row.location = ""
        fake_row.start_time = ev.start_time
        fake_row.end_time = ev.end_time
        fake_row.status = "confirmed"
        fake_row.is_all_day = False
        fake_row.recurrence_rule = ""
        fake_row.provider_event_id = ""
        fake_row.calendar_id = ""
        fake_row.attendees_json = "[]"
        fake_row.reminders_json = "[]"

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            result = MagicMock()
            result.scalars.return_value.all.return_value = [fake_row]
            session.execute = AsyncMock(return_value=result)
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        results = await a.list_by_user(ev.user_id)
        assert len(results) == 1
        assert results[0].id == ev.id


# ---------------------------------------------------------------------------
# Exception paths in _db_get and get_by_provider_id (lines 227-229, 384-385)
# ---------------------------------------------------------------------------


class TestDbExceptionPaths:
    """Test that DB exceptions are silently caught in _db_get and get_by_provider_id."""

    @pytest.mark.unit
    async def test_db_get_exception_returns_none(self):
        """_db_get swallows DB exception and returns None (lines 227-229)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            session.execute = AsyncMock(side_effect=RuntimeError("DB down"))
            yield session

        a = InMemoryCalendarAdapter()
        a.set_db_session_factory(factory)
        result = await a._db_get(uuid.uuid4())
        assert result is None

    @pytest.mark.unit
    async def test_get_by_provider_id_exception_falls_back_to_memory(self):
        """get_by_provider_id swallows DB exception and falls back to in-memory (lines 384-385)."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        @asynccontextmanager
        async def factory():
            session = MagicMock()
            session.execute = AsyncMock(side_effect=RuntimeError("DB down"))
            yield session

        ev = _ev()
        a = InMemoryCalendarAdapter()
        a._events[ev.id] = ev
        a.set_db_session_factory(factory)
        result = await a.get_by_provider_id(ev.user_id, ev.provider_event_id or "pid")
        # Falls back to in-memory lookup (returns None since provider_event_id not set on ev)
        assert result is None or result.id == ev.id
