"""
Unit tests for calendar_tools (agent/tools/calendar_tools.py).
Tests each LangChain tool function by calling its underlying coroutine.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.tools.calendar_tools import create_calendar_tools
from src.application.services.calendar_service import CalendarService
from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.value_objects import TimeSlot

_NOW = datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc)
_UID = uuid.uuid4()


def _event(title="Meeting", location=None) -> CalendarEvent:
    return CalendarEvent(
        id=uuid.uuid4(),
        user_id=_UID,
        title=title,
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
        location=location,
        status=EventStatus.CONFIRMED,
        provider_event_id="prov-123",
    )


def _make_service_mock(
    create_result=None,
    list_result=None,
    update_result=None,
    delete_result=True,
    free_slots=None,
    conflicts=None,
) -> MagicMock:
    svc = MagicMock(spec=CalendarService)
    svc.create_event = AsyncMock(return_value=create_result or _event())
    svc.list_events = AsyncMock(return_value=list_result or [])
    svc.update_event = AsyncMock(return_value=update_result or _event("Updated"))
    svc.delete_event = AsyncMock(return_value=delete_result)
    svc.find_free_slots = AsyncMock(return_value=free_slots or [])
    svc.check_conflicts = AsyncMock(return_value=conflicts or [])
    return svc


def _get_tool(tools, name: str):
    """Get a tool by name from the tools list."""
    for t in tools:
        if t.name == name:
            return t
    raise KeyError(f"Tool '{name}' not found in {[t.name for t in tools]}")


# ---------------------------------------------------------------------------
# create_event tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_tool_returns_success_message():
    svc = _make_service_mock(create_result=_event("Sprint Review"))
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "create_event")

    result = await tool.ainvoke(
        {
            "title": "Sprint Review",
            "start_time": _NOW.isoformat(),
            "end_time": (_NOW + timedelta(hours=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "Sprint Review" in result
    assert "✅" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_with_description_location_attendees():
    svc = _make_service_mock()
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "create_event")

    result = await tool.ainvoke(
        {
            "title": "Board Meeting",
            "start_time": _NOW.isoformat(),
            "end_time": (_NOW + timedelta(hours=2)).isoformat(),
            "description": "Quarterly board meeting",
            "location": "Conference Room A",
            "attendee_emails": ["alice@example.com", "bob@example.com"],
            "user_id": str(_UID),
        }
    )
    assert "✅" in result


# ---------------------------------------------------------------------------
# list_events tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_tool_returns_events():
    ev = _event("Standup", location="Zoom")
    svc = _make_service_mock(list_result=[ev])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "list_events")

    result = await tool.ainvoke(
        {
            "start_date": _NOW.isoformat(),
            "end_date": (_NOW + timedelta(days=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "Standup" in result
    assert "@Zoom" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_tool_empty():
    svc = _make_service_mock(list_result=[])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "list_events")

    result = await tool.ainvoke(
        {
            "start_date": _NOW.isoformat(),
            "end_date": (_NOW + timedelta(days=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "No events found" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_tool_event_without_location():
    ev = _event("No Loc")
    svc = _make_service_mock(list_result=[ev])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "list_events")

    result = await tool.ainvoke(
        {
            "start_date": _NOW.isoformat(),
            "end_date": (_NOW + timedelta(days=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "No Loc" in result


# ---------------------------------------------------------------------------
# update_event tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_tool_returns_updated_message():
    updated_ev = _event("Updated Sprint")
    svc = _make_service_mock(update_result=updated_ev)
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "update_event")

    result = await tool.ainvoke(
        {
            "event_id": "ev-123",
            "title": "Updated Sprint",
            "user_id": str(_UID),
        }
    )
    assert "Updated" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_with_times():
    svc = _make_service_mock()
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "update_event")

    result = await tool.ainvoke(
        {
            "event_id": "ev-123",
            "start_time": _NOW.isoformat(),
            "end_time": (_NOW + timedelta(hours=2)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "✅" in result


# ---------------------------------------------------------------------------
# delete_event tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_tool_success():
    svc = _make_service_mock(delete_result=True)
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "delete_event")

    result = await tool.ainvoke({"event_id": "ev-123", "user_id": str(_UID)})
    assert "deleted" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_tool_failure():
    svc = _make_service_mock(delete_result=False)
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "delete_event")

    result = await tool.ainvoke({"event_id": "ev-999", "user_id": str(_UID)})
    assert "Failed" in result or "❌" in result


# ---------------------------------------------------------------------------
# find_free_slots tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_returns_slots():
    slot = TimeSlot(start=_NOW, end=_NOW + timedelta(hours=1))
    svc = _make_service_mock(free_slots=[slot])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "find_free_slots")

    result = await tool.ainvoke(
        {
            "start_date": _NOW.isoformat(),
            "end_date": (_NOW + timedelta(days=1)).isoformat(),
            "duration_minutes": 30,
            "user_id": str(_UID),
        }
    )
    assert "Available slots" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_empty():
    svc = _make_service_mock(free_slots=[])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "find_free_slots")

    result = await tool.ainvoke(
        {
            "start_date": _NOW.isoformat(),
            "end_date": (_NOW + timedelta(days=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "No free slots" in result


# ---------------------------------------------------------------------------
# check_conflicts tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_conflicts_no_conflicts():
    svc = _make_service_mock(conflicts=[])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "check_conflicts")

    result = await tool.ainvoke(
        {
            "start_time": _NOW.isoformat(),
            "end_time": (_NOW + timedelta(hours=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "No conflicts" in result or "free" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_conflicts_with_conflicts():
    conflicting = _event("Design Review")
    svc = _make_service_mock(conflicts=[conflicting])
    tools = create_calendar_tools(svc)
    tool = _get_tool(tools, "check_conflicts")

    result = await tool.ainvoke(
        {
            "start_time": _NOW.isoformat(),
            "end_time": (_NOW + timedelta(hours=1)).isoformat(),
            "user_id": str(_UID),
        }
    )
    assert "Design Review" in result or "Conflicts" in result


# ---------------------------------------------------------------------------
# create_calendar_tools — list length
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_create_calendar_tools_returns_six_tools():
    svc = _make_service_mock()
    tools = create_calendar_tools(svc)
    assert len(tools) == 6
    names = {t.name for t in tools}
    assert "create_event" in names
    assert "list_events" in names
    assert "update_event" in names
    assert "delete_event" in names
    assert "find_free_slots" in names
    assert "check_conflicts" in names
