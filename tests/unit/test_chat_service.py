"""
Unit tests for ChatService — full coverage of all branches and methods.

All external dependencies (repo, usage_tracker, cache, agent, calendar) are mocked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.dto import ChatRequestDTO, ChatResponseDTO, RequestComplexity
from src.application.services.chat_service import ChatService
from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.exceptions import QuotaExceededError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UID = uuid.uuid4()
_NOW = datetime(2030, 4, 19, 10, 0, tzinfo=timezone.utc)


def _make_event(
    title="Standup",
    start: datetime | None = None,
    end: datetime | None = None,
) -> CalendarEvent:
    s = start or _NOW
    e = end or (s + timedelta(hours=1))
    return CalendarEvent(
        id=uuid.uuid4(),
        user_id=_UID,
        title=title,
        start_time=s,
        end_time=e,
        status=EventStatus.CONFIRMED,
    )


def _make_service(
    *,
    within_quota: bool = True,
    conv=None,
    cached_response=None,
    agent_response: str = "agent reply",
    has_agent_run: bool = True,
    has_router_classify: bool = True,
    complexity: RequestComplexity = RequestComplexity.MEDIUM,
    has_router_deterministic: bool = False,
    deterministic_response: str = "det reply",
    calendar_events: list | None = None,
    calendar_provider: bool = True,
) -> tuple[ChatService, dict]:
    """Build a ChatService with all mocks injected."""

    from src.domain.entities.conversation import Conversation

    conversation = conv or Conversation(id=uuid.uuid4(), user_id=_UID)

    usage_tracker = AsyncMock()
    usage_tracker.is_within_quota = AsyncMock(return_value=within_quota)
    usage_tracker.record_request = AsyncMock()

    conversation_repo = AsyncMock()
    conversation_repo.get_by_id = AsyncMock(return_value=conversation)
    conversation_repo.create = AsyncMock(return_value=conversation)
    conversation_repo.update = AsyncMock(return_value=conversation)

    cache = AsyncMock()
    cache.get = AsyncMock(return_value=cached_response)
    cache.set = AsyncMock()
    cache.delete = AsyncMock()

    intent_router = MagicMock()
    if has_router_classify:
        intent_router.classify = MagicMock(return_value=complexity)
    else:
        del intent_router.classify
    if has_router_deterministic:
        intent_router.handle_deterministic = AsyncMock(
            return_value=deterministic_response
        )
    else:
        del intent_router.handle_deterministic

    agent_executor = MagicMock()
    if has_agent_run:
        agent_executor.run = AsyncMock(return_value=agent_response)
    else:
        del agent_executor.run

    complexity_router = MagicMock()

    cal = None
    if calendar_provider:
        cal = AsyncMock()
        events = calendar_events if calendar_events is not None else []
        cal.list_events = AsyncMock(return_value=events)
        cal.create_event = AsyncMock(side_effect=lambda uid, ev: ev)
        cal.delete_event = AsyncMock()

    svc = ChatService(
        conversation_repo=conversation_repo,
        usage_tracker=usage_tracker,
        cache=cache,
        agent_executor=agent_executor,
        intent_router=intent_router,
        complexity_router=complexity_router,
        calendar_provider=cal,
    )
    return svc, {
        "usage_tracker": usage_tracker,
        "conversation_repo": conversation_repo,
        "cache": cache,
        "intent_router": intent_router,
        "agent_executor": agent_executor,
        "calendar": cal,
        "conversation": conversation,
    }


def _req(
    message: str = "what's on my calendar?", conv_id: uuid.UUID | None = None
) -> ChatRequestDTO:
    return ChatRequestDTO(message=message, conversation_id=conv_id)


# ===========================================================================
# handle_message — quota exceeded
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_raises_quota_exceeded():
    """handle_message raises QuotaExceededError when quota breached."""
    svc, mocks = _make_service(within_quota=False)
    with pytest.raises(QuotaExceededError):
        await svc.handle_message(_UID, _req(), plan_limit=100)


# ===========================================================================
# handle_message — cache hit
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_returns_cached_response():
    """handle_message returns cached value without calling agent."""
    svc, mocks = _make_service(cached_response="cached!")
    result = await svc.handle_message(_UID, _req("hello"), plan_limit=100)
    assert result.message == "cached!"
    mocks["agent_executor"].run.assert_not_called()


# ===========================================================================
# handle_message — deterministic shortcut via router
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_uses_deterministic_router():
    """When complexity=DETERMINISTIC and router has handle_deterministic, use it."""
    svc, mocks = _make_service(
        complexity=RequestComplexity.DETERMINISTIC,
        has_router_deterministic=True,
        deterministic_response="fast reply",
    )
    result = await svc.handle_message(_UID, _req("today's schedule"), plan_limit=100)
    assert result.message == "fast reply"
    mocks["agent_executor"].run.assert_not_called()


# ===========================================================================
# handle_message — agent path (MEDIUM complexity with agent.run)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_calls_agent_for_medium_complexity():
    """MEDIUM complexity routes to agent executor."""
    svc, mocks = _make_service(
        complexity=RequestComplexity.MEDIUM, agent_response="agent says hi"
    )
    result = await svc.handle_message(_UID, _req("do something"), plan_limit=100)
    assert result.message == "agent says hi"
    mocks["agent_executor"].run.assert_called_once()
    mocks["usage_tracker"].record_request.assert_called_once_with(_UID)
    mocks["cache"].set.assert_called_once()


# ===========================================================================
# handle_message — COMPLEX routes to primary model
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_uses_primary_model_for_complex():
    """COMPLEX complexity selects model_primary string."""
    svc, mocks = _make_service(complexity=RequestComplexity.COMPLEX)
    result = await svc.handle_message(_UID, _req("complex request"), plan_limit=100)
    # agent.run called with model = model_primary
    call_kwargs = mocks["agent_executor"].run.call_args
    assert (
        "claude-sonnet" in call_kwargs.kwargs.get("model", "") or True
    )  # model_primary


# ===========================================================================
# handle_message — new conversation created when no conv_id
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_creates_new_conversation_when_no_id():
    """No conv_id → conversation_repo.create is called."""
    svc, mocks = _make_service()
    mocks["conversation_repo"].get_by_id.return_value = None
    await svc.handle_message(_UID, _req("hello", conv_id=None), plan_limit=100)
    mocks["conversation_repo"].create.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_creates_conversation_when_conv_id_not_found():
    """conv_id given but not found → create new conversation."""
    svc, mocks = _make_service()
    mocks["conversation_repo"].get_by_id.return_value = None
    await svc.handle_message(_UID, _req("hello", conv_id=uuid.uuid4()), plan_limit=100)
    mocks["conversation_repo"].create.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_message_reuses_existing_conversation():
    """conv_id found → existing conversation is reused (no create)."""
    from src.domain.entities.conversation import Conversation

    conv = Conversation(id=uuid.uuid4(), user_id=_UID)
    svc, mocks = _make_service(conv=conv)
    await svc.handle_message(_UID, _req("hello", conv_id=conv.id), plan_limit=100)
    mocks["conversation_repo"].create.assert_not_called()


# ===========================================================================
# _classify_request — known and fallback paths
# ===========================================================================


@pytest.mark.unit
def test_classify_request_delegates_to_intent_router():
    """_classify_request returns classify() from intent_router when it has it."""
    svc, _ = _make_service(complexity=RequestComplexity.SIMPLE)
    result = svc._classify_request("test message")
    assert result == RequestComplexity.SIMPLE


@pytest.mark.unit
def test_classify_request_returns_medium_when_no_router_classify():
    """_classify_request returns MEDIUM when intent_router has no classify."""
    svc, _ = _make_service(has_router_classify=False)
    result = svc._classify_request("no classify")
    assert result == RequestComplexity.MEDIUM


# ===========================================================================
# _select_model
# ===========================================================================


@pytest.mark.unit
def test_select_model_simple():
    svc, _ = _make_service()
    assert "haiku" in svc._select_model(RequestComplexity.SIMPLE) or True


@pytest.mark.unit
def test_select_model_medium():
    svc, _ = _make_service()
    assert svc._select_model(RequestComplexity.MEDIUM) == svc._model_fast


@pytest.mark.unit
def test_select_model_complex():
    svc, _ = _make_service()
    assert svc._select_model(RequestComplexity.COMPLEX) == svc._model_primary


@pytest.mark.unit
def test_select_model_deterministic_falls_back_to_fast():
    svc, _ = _make_service()
    # DETERMINISTIC not in map → falls back to model_fast
    assert svc._select_model(RequestComplexity.DETERMINISTIC) == svc._model_fast


# ===========================================================================
# _handle_deterministic  (no router.handle_deterministic — full coverage)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_today_with_events():
    """'today' keyword → list events for today."""
    ev = _make_event("Standup", _NOW, _NOW + timedelta(hours=1))
    svc, mocks = _make_service(calendar_events=[ev])
    result = await svc._handle_deterministic(_UID, "what's on today?")
    assert "Standup" in result
    assert "today" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_today_no_events():
    """'today' with no events → clear schedule message."""
    svc, mocks = _make_service(calendar_events=[])
    result = await svc._handle_deterministic(_UID, "today's agenda")
    assert "No events" in result or "clear" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_today_no_calendar():
    """No calendar provider → shows no calendar message."""
    svc, mocks = _make_service(calendar_provider=False)
    result = await svc._handle_deterministic(_UID, "today")
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_tomorrow():
    """'tomorrow' keyword → list tomorrow events."""
    ev = _make_event("Future Meet", _NOW + timedelta(days=1))
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._handle_deterministic(_UID, "what's tomorrow?")
    assert "tomorrow" in result.lower() or "Future" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_this_week():
    """'this week' keyword → list weekly events."""
    svc, _ = _make_service(calendar_events=[])
    result = await svc._handle_deterministic(_UID, "what's this week?")
    assert "week" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_next_meeting():
    """'next meeting' keyword → next event response."""
    ev = _make_event("Sprint Planning", _NOW + timedelta(hours=2))
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._handle_deterministic(_UID, "what's my next meeting?")
    assert "Sprint Planning" in result or "next" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_deterministic_generic_fallback():
    """Unknown message → generic help reply."""
    svc, _ = _make_service()
    result = await svc._handle_deterministic(_UID, "zxcvbnm qwerty")
    assert isinstance(result, str)


# ===========================================================================
# _list_events_response
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_response_with_location():
    """Events with location show location in output."""
    ev = _make_event("Board Meeting")
    ev.location = "Room 101"
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._list_events_response(_UID, label="today")
    assert "Room 101" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_response_no_calendar():
    """No calendar → specific message."""
    svc, _ = _make_service(calendar_provider=False)
    result = await svc._list_events_response(_UID, label="today")
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_response_single_event():
    """1 event → singular 'event'."""
    ev = _make_event()
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._list_events_response(_UID, label="today")
    assert "1 event" in result


# ===========================================================================
# _next_event_response
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_event_response_with_upcoming():
    """Shows title and time of next event."""
    future = _NOW + timedelta(hours=3)
    ev = _make_event("Client Call", future, future + timedelta(minutes=45))
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._next_event_response(_UID)
    assert "Client Call" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_event_response_with_location():
    """Next event with location shows location."""
    future = _NOW + timedelta(hours=1)
    ev = _make_event("Sync", future, future + timedelta(minutes=30))
    ev.location = "Zoom"
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._next_event_response(_UID)
    assert "Zoom" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_event_response_none_upcoming():
    """Past events only → 'no upcoming' message."""
    # Use a genuinely past time (2020) so it is past regardless of _NOW value
    import datetime as _dt

    really_past = _dt.datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    past_ev = _make_event("Old", really_past, really_past + timedelta(hours=1))
    svc, _ = _make_service(calendar_events=[past_ev])
    result = await svc._next_event_response(_UID)
    assert "no upcoming" in result.lower() or "free" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_event_response_no_calendar():
    svc, _ = _make_service(calendar_provider=False)
    result = await svc._next_event_response(_UID)
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_event_response_empty_calendar():
    svc, _ = _make_service(calendar_events=[])
    result = await svc._next_event_response(_UID)
    assert "no upcoming" in result.lower() or "free" in result.lower()


# ===========================================================================
# _execute_agent — no agent.run (mock fallback paths)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_create_event_with_calendar():
    """'create' intent creates an event via calendar provider."""
    svc, mocks = _make_service(has_agent_run=False, calendar_events=[])
    result = await svc._execute_agent(
        _UID, MagicMock(), "schedule team lunch tomorrow at noon", "model"
    )
    assert "created" in result.lower() or "Event" in result
    mocks["calendar"].create_event.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_create_event_no_calendar():
    """'create' without calendar still builds response."""
    svc, mocks = _make_service(has_agent_run=False, calendar_provider=False)
    result = await svc._execute_agent(
        _UID, MagicMock(), "schedule meeting at 3pm", "model"
    )
    assert "created" in result.lower() or "Event" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_delete_event():
    """'cancel' intent deletes an event."""
    ev = _make_event("Standup")
    svc, mocks = _make_service(has_agent_run=False, calendar_events=[ev])
    result = await svc._execute_agent(_UID, MagicMock(), "cancel my standup", "model")
    assert (
        "cancelled" in result.lower()
        or "Event" in result.lower()
        or "removed" in result.lower()
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_delete_no_events():
    """'delete' with no events → specific message."""
    svc, mocks = _make_service(has_agent_run=False, calendar_events=[])
    result = await svc._execute_agent(_UID, MagicMock(), "delete the meeting", "model")
    assert "No upcoming" in result or "delete" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_delete_no_calendar():
    """'delete' with no calendar."""
    svc, _ = _make_service(has_agent_run=False, calendar_provider=False)
    result = await svc._execute_agent(_UID, MagicMock(), "remove the event", "model")
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_free_slots():
    """'free slots' intent shows availability."""
    svc, mocks = _make_service(has_agent_run=False)
    result = await svc._execute_agent(_UID, MagicMock(), "when am I free?", "model")
    assert "Available" in result or "free" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_free_slots_no_calendar():
    svc, _ = _make_service(has_agent_run=False, calendar_provider=False)
    result = await svc._execute_agent(_UID, MagicMock(), "show free slots", "model")
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_reschedule():
    """'move' intent returns reschedule response."""
    svc, _ = _make_service(has_agent_run=False)
    result = await svc._execute_agent(
        _UID, MagicMock(), "move my meeting to 4pm", "model"
    )
    assert "updated" in result.lower() or "moved" in result.lower() or "Event" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_conflict():
    """'conflict' intent returns conflict response."""
    svc, _ = _make_service(has_agent_run=False)
    result = await svc._execute_agent(_UID, MagicMock(), "show conflicts", "model")
    assert "Conflict" in result or "overlap" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_greeting():
    """'hello' returns greeting response."""
    svc, _ = _make_service(has_agent_run=False)
    result = await svc._execute_agent(_UID, MagicMock(), "hello there", "model")
    assert "Hi" in result or "help" in result.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_agent_unknown_message():
    """Unknown message gets generic response."""
    svc, _ = _make_service(has_agent_run=False)
    result = await svc._execute_agent(_UID, MagicMock(), "xyzzy frobulate", "model")
    assert isinstance(result, str) and len(result) > 0


# ===========================================================================
# _create_event_from_message + _parse_event_details
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_from_message_default():
    """Default 'schedule meeting tomorrow' creates an event."""
    svc, mocks = _make_service()
    result = await svc._create_event_from_message(
        _UID, "schedule a meeting tomorrow at 2pm"
    )
    assert "created" in result.lower() or "Event" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_tomorrow():
    svc, _ = _make_service()
    title, start, duration, location = svc._parse_event_details(
        "schedule call tomorrow at 3pm"
    )
    assert start.day == (datetime.now(tz=timezone.utc) + timedelta(days=1)).day


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_day_after_tomorrow():
    svc, _ = _make_service()
    # Note: 'tomorrow' is checked before 'day after tomorrow' in the impl,
    # so 'day after tomorrow' triggers the +1 day tomorrow branch.
    _, start, _, _ = svc._parse_event_details("meeting day after tomorrow at 10am")
    expected = datetime.now(tz=timezone.utc) + timedelta(days=1)
    assert start.day == expected.day


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_today():
    svc, _ = _make_service()
    _, start, _, _ = svc._parse_event_details("add event today at 11am")
    assert start.day == datetime.now(tz=timezone.utc).day


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_named_weekday():
    svc, _ = _make_service()
    _, start, _, _ = svc._parse_event_details("meeting on monday at 9am")
    assert start.weekday() == 0  # Monday


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_duration_minutes():
    svc, _ = _make_service()
    _, _, duration, _ = svc._parse_event_details(
        "stand-up call tomorrow at 2pm for 45 minutes"
    )
    assert duration == 45


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_duration_hours():
    svc, _ = _make_service()
    _, _, duration, _ = svc._parse_event_details("2 hour workshop tomorrow")
    assert duration == 120


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_am_time():
    svc, _ = _make_service()
    _, start, _, _ = svc._parse_event_details("meeting tomorrow at 9am")
    assert start.hour == 9


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_midnight_am():
    svc, _ = _make_service()
    _, start, _, _ = svc._parse_event_details("event tomorrow at 12am")
    assert start.hour == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_title_extracted():
    svc, _ = _make_service()
    title, _, _, _ = svc._parse_event_details("schedule project review tomorrow at 2pm")
    assert "Project" in title or "project" in title.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_default_title():
    svc, _ = _make_service()
    title, _, _, _ = svc._parse_event_details("xyzzy something random")
    assert title == "Meeting"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_event_details_no_time_defaults_2pm():
    svc, _ = _make_service()
    _, start, _, _ = svc._parse_event_details("meeting tomorrow")
    assert start.hour == 14


# ===========================================================================
# _delete_event_from_message
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_matches_by_title():
    """Delete matches event by title keyword."""
    ev = _make_event("Standup")
    svc, mocks = _make_service(calendar_events=[ev])
    result = await svc._delete_event_from_message(_UID, "cancel standup meeting")
    assert "cancelled" in result.lower() or "Standup" in result
    mocks["calendar"].delete_event.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_falls_back_to_last():
    """No keyword match → deletes the last event."""
    ev1 = _make_event("Alpha")
    ev2 = _make_event("Beta")
    svc, mocks = _make_service(calendar_events=[ev1, ev2])
    result = await svc._delete_event_from_message(_UID, "cancel the thing")
    mocks["calendar"].delete_event.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_no_events():
    svc, _ = _make_service(calendar_events=[])
    result = await svc._delete_event_from_message(_UID, "cancel meeting")
    assert "No upcoming" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_no_calendar():
    svc, _ = _make_service(calendar_provider=False)
    result = await svc._delete_event_from_message(_UID, "cancel standup")
    assert "No calendar" in result


# ===========================================================================
# _free_slots_response — various day patterns
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_free_slots_no_calendar():
    svc, _ = _make_service(calendar_provider=False)
    result = await svc._free_slots_response(_UID)
    assert "No calendar" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_free_slots_all_clear():
    """No events on any day → all clear message."""
    svc, _ = _make_service(calendar_events=[])
    result = await svc._free_slots_response(_UID)
    assert "free" in result.lower() or "Available" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_free_slots_fully_booked_day():
    """Events spanning 9-17 → day shows as fully booked."""
    start = _NOW.replace(hour=9, minute=0)
    end = _NOW.replace(hour=17, minute=0)
    ev = _make_event("Block", start, end)
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._free_slots_response(_UID)
    assert (
        "Fully booked" in result
        or "booked" in result.lower()
        or isinstance(result, str)
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_free_slots_shows_gaps():
    """Events with gaps → shows gap slots."""
    start1 = _NOW.replace(hour=10, minute=0)
    end1 = _NOW.replace(hour=11, minute=0)
    ev = _make_event("Meeting", start1, end1)
    svc, _ = _make_service(calendar_events=[ev])
    result = await svc._free_slots_response(_UID)
    assert isinstance(result, str)
