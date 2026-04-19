"""
Unit tests for MicrosoftCalendarAdapter (infrastructure/calendar_providers/microsoft_calendar.py).
All HTTP calls are mocked with httpx.AsyncClient.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.exceptions import CalendarProviderError
from src.infrastructure.calendar_providers.microsoft_calendar import (
    MicrosoftCalendarAdapter,
)

_UID = uuid.uuid4()
_NOW = datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc)


def _adapter(token: str = "tok123") -> MicrosoftCalendarAdapter:
    return MicrosoftCalendarAdapter(access_token=token)


def _event(**kw) -> CalendarEvent:
    s = kw.pop("start_time", _NOW)
    e = kw.pop("end_time", _NOW + timedelta(hours=1))
    return CalendarEvent(
        id=uuid.uuid4(),
        user_id=_UID,
        title=kw.pop("title", "Meeting"),
        start_time=s,
        end_time=e,
        status=EventStatus.CONFIRMED,
        provider_event_id=kw.pop("provider_event_id", "ev-id-123"),
        **kw,
    )


def _ms_event_payload(title="Meeting", event_id="ev-123") -> dict:
    return {
        "id": event_id,
        "subject": title,
        "bodyPreview": "description",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "location": {"displayName": "Room A"},
        "attendees": [
            {
                "emailAddress": {"address": "bob@example.com", "name": "Bob"},
                "status": {"response": "accepted"},
            }
        ],
        "showAs": "busy",
        "onlineMeeting": {"joinUrl": "https://teams.meeting"},
        "isCancelled": False,
        "isOrganizer": True,
    }


def _mock_client(response_data=None, status_code=200, raise_exc=None):
    """Build a context-manager mock for httpx.AsyncClient."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = response_data or {}
    resp.raise_for_status = MagicMock()
    if raise_exc:
        resp.raise_for_status.side_effect = raise_exc

    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.patch = AsyncMock(return_value=resp)
    client.delete = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_headers_include_bearer_token():
    adapter = _adapter("my_token")
    headers = adapter._headers()
    assert headers["Authorization"] == "Bearer my_token"
    assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_returns_events():
    adapter = _adapter()
    payload = {"value": [_ms_event_payload("Standup", "ev-1")]}
    mock_client = _mock_client(response_data=payload)

    with patch("httpx.AsyncClient", return_value=mock_client):
        events = await adapter.list_events(_UID, _NOW, _NOW + timedelta(days=1))

    assert len(events) == 1
    assert events[0].title == "Standup"
    assert events[0].provider_event_id == "ev-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_raises_calendar_provider_error():
    adapter = _adapter()
    mock_client = _mock_client(raise_exc=Exception("network error"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(CalendarProviderError):
            await adapter.list_events(_UID, _NOW, _NOW + timedelta(days=1))


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_returns_event():
    adapter = _adapter()
    mock_client = _mock_client(response_data=_ms_event_payload(), status_code=200)

    with patch("httpx.AsyncClient", return_value=mock_client):
        event = await adapter.get_event(_UID, "ev-123")

    assert event is not None
    assert event.title == "Meeting"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_returns_none_on_404():
    adapter = _adapter()
    mock_client = _mock_client(status_code=404)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.get_event(_UID, "missing-id")

    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_raises_on_other_error():
    adapter = _adapter()
    mock_client = _mock_client(raise_exc=Exception("fail"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(CalendarProviderError):
            await adapter.get_event(_UID, "ev-id")


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_sets_provider_event_id():
    adapter = _adapter()
    ev = _event(title="New Meeting")
    mock_client = _mock_client(response_data={"id": "new-ev-id"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.create_event(_UID, ev)

    assert result.provider_event_id == "new-ev-id"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_raises_on_error():
    adapter = _adapter()
    ev = _event()
    mock_client = _mock_client(raise_exc=Exception("API error"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(CalendarProviderError):
            await adapter.create_event(_UID, ev)


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_returns_event():
    adapter = _adapter()
    ev = _event(provider_event_id="ev-abc")
    mock_client = _mock_client(response_data={})

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.update_event(_UID, ev)

    assert result is ev


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_raises_on_error():
    adapter = _adapter()
    ev = _event(provider_event_id="ev-abc")
    mock_client = _mock_client(raise_exc=Exception("error"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(CalendarProviderError):
            await adapter.update_event(_UID, ev)


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_returns_true_on_204():
    adapter = _adapter()
    mock_client = _mock_client(status_code=204)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.delete_event(_UID, "ev-id")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_returns_false_on_other_status():
    adapter = _adapter()
    mock_client = _mock_client(status_code=404)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.delete_event(_UID, "ev-id")

    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_returns_false_on_exception():
    adapter = _adapter()
    mock_client = _mock_client(raise_exc=Exception("connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await adapter.delete_event(_UID, "ev-id")

    assert result is False


# ---------------------------------------------------------------------------
# find_free_slots
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_returns_slots_when_gap_exists():
    adapter = _adapter()
    # One event 11-12, leaving 10-11 and 12-end free
    ev = _event(start_time=_NOW.replace(hour=11), end_time=_NOW.replace(hour=12))
    payload = {"value": [_ms_event_payload("Blocker", "ev-1")]}

    # We need to patch list_events to return our event
    with patch.object(
        adapter,
        "list_events",
        AsyncMock(return_value=[ev]),
    ):
        start = _NOW.replace(hour=9)
        end = _NOW.replace(hour=17)
        slots = await adapter.find_free_slots(_UID, start, end, duration_minutes=30)

    assert len(slots) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_empty_when_no_gaps():
    adapter = _adapter()
    start = _NOW.replace(hour=9)
    end = _NOW.replace(hour=17)
    # One event blocking the whole period
    ev = _event(start_time=start, end_time=end)

    with patch.object(adapter, "list_events", AsyncMock(return_value=[ev])):
        slots = await adapter.find_free_slots(_UID, start, end, duration_minutes=30)

    assert slots == []


# ---------------------------------------------------------------------------
# _parse_event — static method
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_event_handles_missing_fields():
    data = {}
    event = MicrosoftCalendarAdapter._parse_event(data, _UID)
    assert event is not None
    assert event.title == ""


@pytest.mark.unit
def test_parse_event_maps_declined_to_cancelled():
    """responseStatus.response == 'declined' → CANCELLED."""
    data = {
        "id": "ev-1",
        "subject": "Declined Event",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "attendees": [],
        "responseStatus": {"response": "declined"},
    }
    event = MicrosoftCalendarAdapter._parse_event(data, _UID)
    assert event.status == EventStatus.CANCELLED


@pytest.mark.unit
def test_parse_event_maps_tentatively_accepted():
    """responseStatus.response == 'tentativelyAccepted' → TENTATIVE."""
    data = {
        "subject": "Maybe",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "attendees": [],
        "responseStatus": {"response": "tentativelyAccepted"},
    }
    event = MicrosoftCalendarAdapter._parse_event(data, _UID)
    assert event.status == EventStatus.TENTATIVE


@pytest.mark.unit
def test_parse_event_maps_all_day_flag():
    """isAllDay field is mapped on the event."""
    data = _ms_event_payload()
    data["isAllDay"] = True
    event = MicrosoftCalendarAdapter._parse_event(data, _UID)
    assert event.is_all_day is True


@pytest.mark.unit
def test_parse_event_attendee_status_mapping():
    data = {
        "subject": "Meet",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "showAs": "busy",
        "isCancelled": False,
        "attendees": [
            {
                "emailAddress": {"address": "a@x.com"},
                "status": {"response": "declined"},
            },
            {
                "emailAddress": {"address": "b@x.com"},
                "status": {"response": "tentativelyAccepted"},
            },
            {
                "emailAddress": {"address": "c@x.com"},
                "status": {"response": "organizer"},
            },
        ],
    }
    event = MicrosoftCalendarAdapter._parse_event(data, _UID)
    assert len(event.attendees) == 3
