"""Tests for src/infrastructure/calendar_providers/google_calendar.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.calendar_event import Attendee, CalendarEvent, EventStatus
from src.domain.exceptions import CalendarProviderError
from src.domain.value_objects import TimeSlot

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_adapter(user=None):
    if user is None:
        mock_user = MagicMock()
        mock_user.google_access_token = "access_token"
        mock_user.google_refresh_token = "refresh_token"
    else:
        mock_user = user

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=mock_user)

    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    adapter = GoogleCalendarAdapter(
        user_repository=repo,
        client_id="cid",
        client_secret="csec",
    )
    return adapter, repo


def _make_service():
    svc = MagicMock()
    events_resource = MagicMock()
    svc.events = MagicMock(return_value=events_resource)
    return svc, events_resource


def _google_event(title="Test", event_id="ev1", status="confirmed"):
    return {
        "id": event_id,
        "summary": title,
        "description": "desc",
        "location": "Conf Room",
        "status": status,
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "attendees": [],
    }


# ---------------------------------------------------------------------------
# _get_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_raises_when_no_user():
    adapter, repo = _make_adapter()
    repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(CalendarProviderError, match="No valid credentials"):
        await adapter._get_service(_UID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_raises_when_no_access_token():
    user = MagicMock()
    user.google_access_token = None
    adapter, _ = _make_adapter(user=user)

    with pytest.raises(CalendarProviderError, match="No valid credentials"):
        await adapter._get_service(_UID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_builds_google_client():
    adapter, _ = _make_adapter()

    mock_service = MagicMock()
    with (
        patch("src.infrastructure.calendar_providers.google_calendar.Credentials"),
        patch(
            "src.infrastructure.calendar_providers.google_calendar.build",
            return_value=mock_service,
        ) as mock_build,
    ):
        result = await adapter._get_service(_UID)

    mock_build.assert_called_once_with(
        "calendar",
        "v3",
        credentials=mock_build.call_args[1].get("credentials")
        or mock_build.call_args[0][2],
    )
    assert result is mock_service


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_returns_parsed_events():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.list.return_value.execute.return_value = {
        "items": [_google_event("Meeting", "ev1"), _google_event("Lunch", "ev2")]
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        results = await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=2))

    assert len(results) == 2
    assert results[0].title == "Meeting"
    assert results[1].title == "Lunch"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_raises_calendar_provider_error_on_exception():
    adapter, _ = _make_adapter()

    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("API down"))
    ):
        with pytest.raises(CalendarProviderError):
            await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=1))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_returns_empty_list():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.list.return_value.execute.return_value = {"items": []}

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        results = await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=1))

    assert results == []


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_returns_event():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.get.return_value.execute.return_value = _google_event("Meeting")

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        event = await adapter.get_event(_UID, "ev1")

    assert event is not None
    assert event.title == "Meeting"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_returns_none_on_exception():
    adapter, _ = _make_adapter()

    with patch.object(adapter, "_get_service", AsyncMock(side_effect=Exception("404"))):
        event = await adapter.get_event(_UID, "missing")

    assert event is None


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_sets_provider_id():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.insert.return_value.execute.return_value = {"id": "new-ev-1"}

    event = CalendarEvent(
        user_id=_UID,
        title="New Event",
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
    )

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        result = await adapter.create_event(_UID, event)

    assert result.provider_event_id == "new-ev-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_raises_on_api_error():
    adapter, _ = _make_adapter()

    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("quota"))
    ):
        with pytest.raises(CalendarProviderError):
            await adapter.create_event(
                _UID,
                CalendarEvent(
                    user_id=_UID,
                    title="X",
                    start_time=_NOW,
                    end_time=_NOW + timedelta(hours=1),
                ),
            )


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_returns_event():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.update.return_value.execute.return_value = {}

    event = CalendarEvent(
        user_id=_UID,
        title="Updated",
        provider_event_id="ev-existing",
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
    )

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        result = await adapter.update_event(_UID, event)

    assert result is event


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_raises_on_api_error():
    adapter, _ = _make_adapter()

    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("auth"))
    ):
        with pytest.raises(CalendarProviderError):
            await adapter.update_event(
                _UID,
                CalendarEvent(
                    user_id=_UID,
                    title="X",
                    start_time=_NOW,
                    end_time=_NOW + timedelta(hours=1),
                ),
            )


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_returns_true_on_success():
    adapter, _ = _make_adapter()
    svc, events_res = _make_service()
    events_res.delete.return_value.execute.return_value = None

    with patch.object(adapter, "_get_service", AsyncMock(return_value=svc)):
        result = await adapter.delete_event(_UID, "ev1")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_returns_false_on_exception():
    adapter, _ = _make_adapter()

    with patch.object(adapter, "_get_service", AsyncMock(side_effect=Exception("404"))):
        result = await adapter.delete_event(_UID, "ev1")

    assert result is False


# ---------------------------------------------------------------------------
# find_free_slots
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_returns_gap_between_events():
    adapter, _ = _make_adapter()

    start = _NOW
    mid1 = _NOW + timedelta(hours=1)
    mid2 = _NOW + timedelta(hours=2)
    end = _NOW + timedelta(hours=4)

    ev1 = CalendarEvent(
        user_id=_UID,
        title="A",
        start_time=start,
        end_time=mid1,
    )
    ev2 = CalendarEvent(
        user_id=_UID,
        title="B",
        start_time=mid2,
        end_time=_NOW + timedelta(hours=3),
    )

    with patch.object(adapter, "list_events", AsyncMock(return_value=[ev1, ev2])):
        slots = await adapter.find_free_slots(_UID, start, end, duration_minutes=30)

    # Gap: mid1 to mid2 = 1 hour, plus gap after ev2
    assert len(slots) >= 1
    assert all(isinstance(s, TimeSlot) for s in slots)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_no_events_returns_whole_window():
    adapter, _ = _make_adapter()
    end = _NOW + timedelta(hours=4)

    with patch.object(adapter, "list_events", AsyncMock(return_value=[])):
        slots = await adapter.find_free_slots(_UID, _NOW, end, duration_minutes=30)

    assert len(slots) == 1
    assert slots[0].start == _NOW
    assert slots[0].end == end


# ---------------------------------------------------------------------------
# _parse_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_event_all_day():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    data = {
        "id": "allday1",
        "summary": "Holiday",
        "start": {"date": "2024-06-01"},
        "end": {"date": "2024-06-02"},
        "status": "confirmed",
    }
    event = GoogleCalendarAdapter._parse_event(data, _UID)
    assert event.is_all_day is True
    assert event.title == "Holiday"


@pytest.mark.unit
def test_parse_event_with_attendees():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    data = {
        "id": "ev1",
        "summary": "Meeting",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "status": "confirmed",
        "attendees": [
            {"email": "a@b.com", "displayName": "Alice", "responseStatus": "accepted"},
            {"email": "b@c.com", "organizer": True, "responseStatus": "accepted"},
        ],
    }
    event = GoogleCalendarAdapter._parse_event(data, _UID)
    assert len(event.attendees) == 2
    assert event.attendees[0].email == "a@b.com"
    assert event.attendees[1].is_organizer is True


@pytest.mark.unit
def test_parse_event_maps_status_tentative():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    data = _google_event(status="tentative")
    event = GoogleCalendarAdapter._parse_event(data, _UID)
    assert event.status == EventStatus.TENTATIVE


@pytest.mark.unit
def test_parse_event_maps_status_cancelled():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    data = _google_event(status="cancelled")
    event = GoogleCalendarAdapter._parse_event(data, _UID)
    assert event.status == EventStatus.CANCELLED


@pytest.mark.unit
def test_parse_event_unknown_status_defaults_to_confirmed():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    data = _google_event(status="unknown_status")
    event = GoogleCalendarAdapter._parse_event(data, _UID)
    assert event.status == EventStatus.CONFIRMED


# ---------------------------------------------------------------------------
# _to_google_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_google_event_basic():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    event = CalendarEvent(
        user_id=_UID,
        title="Test",
        description="Desc",
        location="Room 1",
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
    )
    body = GoogleCalendarAdapter._to_google_event(event)
    assert body["summary"] == "Test"
    assert body["description"] == "Desc"
    assert body["location"] == "Room 1"
    assert "start" in body


@pytest.mark.unit
def test_to_google_event_all_day():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    event = CalendarEvent(
        user_id=_UID,
        title="Holiday",
        start_time=datetime(2024, 6, 1),
        end_time=datetime(2024, 6, 2),
        is_all_day=True,
    )
    body = GoogleCalendarAdapter._to_google_event(event)
    assert "date" in body["start"]
    assert "dateTime" not in body["start"]


@pytest.mark.unit
def test_to_google_event_naive_datetime_adds_z():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    naive_time = datetime(2024, 6, 1, 10, 0)  # No tzinfo
    event = CalendarEvent(
        user_id=_UID,
        title="Test",
        start_time=naive_time,
        end_time=naive_time + timedelta(hours=1),
    )
    body = GoogleCalendarAdapter._to_google_event(event)
    assert body["start"]["dateTime"].endswith("Z")


@pytest.mark.unit
def test_to_google_event_with_attendees():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    event = CalendarEvent(
        user_id=_UID,
        title="Meeting",
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
        attendees=[Attendee(email="x@y.com", name="X")],
    )
    body = GoogleCalendarAdapter._to_google_event(event)
    assert len(body["attendees"]) == 1
    assert body["attendees"][0]["email"] == "x@y.com"


@pytest.mark.unit
def test_to_google_event_with_reminders():
    from src.infrastructure.calendar_providers.google_calendar import (
        GoogleCalendarAdapter,
    )

    reminder = MagicMock()
    reminder.method = "email"
    reminder.minutes_before = 30
    event = CalendarEvent(
        user_id=_UID,
        title="Reminder Test",
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
        reminders=[reminder],
    )
    body = GoogleCalendarAdapter._to_google_event(event)
    assert "reminders" in body
    assert body["reminders"]["overrides"][0]["method"] == "email"
