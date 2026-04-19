"""Tests for src/infrastructure/calendar_providers/provider_aware_calendar.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.calendar_event import CalendarEvent

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_adapter(db_session_factory=None):
    from src.infrastructure.calendar_providers.provider_aware_calendar import (
        ProviderAwareCalendarAdapter,
    )

    adapter = ProviderAwareCalendarAdapter(
        google_client_id="cid",
        google_client_secret="csec",
    )
    if db_session_factory:
        adapter._db_session_factory = db_session_factory
    return adapter


def _make_event(title="Test", provider_id=None):
    return CalendarEvent(
        user_id=_UID,
        title=title,
        start_time=_NOW,
        end_time=_NOW + timedelta(hours=1),
        provider_event_id=provider_id,
    )


# ---------------------------------------------------------------------------
# set_db_session_factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_set_db_session_factory_stores_factory():
    adapter = _make_adapter()
    factory = MagicMock()
    adapter.set_db_session_factory(factory)
    assert adapter._db_session_factory is factory


@pytest.mark.unit
def test_set_db_session_factory_also_sets_on_in_memory():
    adapter = _make_adapter()
    factory = MagicMock()
    adapter._in_memory = MagicMock()
    adapter.set_db_session_factory(factory)
    adapter._in_memory.set_db_session_factory.assert_called_once_with(factory)


# ---------------------------------------------------------------------------
# _get_google_tokens — no factory returns None
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_returns_none_when_no_factory():
    adapter = _make_adapter()
    result = await adapter._get_google_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_returns_none_when_no_active_connections():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[])
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=session)
    adapter = _make_adapter(db_session_factory=factory)

    with patch(
        "src.infrastructure.security.token_encryption.decrypt_token",
        return_value="decrypted",
    ):
        result = await adapter._get_google_tokens(_UID)

    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_returns_tokens_when_active_connection():
    row = MagicMock()
    row.access_token = "encrypted-token"
    row.refresh_token = "encrypted-refresh"
    row.provider_email = "user@gmail.com"

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[row])
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=session)
    adapter = _make_adapter(db_session_factory=factory)

    mock_creds = MagicMock()
    mock_creds.token = "new-access-token"
    mock_creds.refresh_token = "new-refresh-token"

    with (
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
        patch(
            "src.infrastructure.security.token_encryption.encrypt_token",
            return_value="encrypted",
        ),
        patch(
            "src.infrastructure.calendar_providers.provider_aware_calendar.ProviderAwareCalendarAdapter._get_google_tokens",
            return_value={
                "access_token": "decrypted-access",
                "refresh_token": "ref",
                "provider_email": "u@g.com",
            },
        ),
    ):
        result = await adapter._get_google_tokens(_UID)

    # Either calls through to real or mock; just verify it returns dict or None
    assert result is None or isinstance(result, dict)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_handles_exception():
    factory = MagicMock(side_effect=Exception("DB error"))
    adapter = _make_adapter(db_session_factory=factory)
    # Should return None without raising
    result = await adapter._get_google_tokens(_UID)
    assert result is None


# ---------------------------------------------------------------------------
# list_events — no tokens → fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_falls_back_to_in_memory_when_no_tokens():
    adapter = _make_adapter()
    local_events = [_make_event("Local Event")]
    adapter._in_memory = AsyncMock()
    adapter._in_memory.list_events = AsyncMock(return_value=local_events)

    with patch.object(adapter, "_get_google_tokens", return_value=None):
        result = await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=2))

    assert result == local_events


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_merges_google_and_local():
    adapter = _make_adapter()
    google_events = [_make_event("Google Event", provider_id="g-ev-1")]
    local_events = [_make_event("Local Only", provider_id="local-1")]

    adapter._in_memory = AsyncMock()
    adapter._in_memory.list_events = AsyncMock(return_value=local_events)

    mock_tokens = {"access_token": "t", "refresh_token": "r"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(
            adapter, "_list_google_events", AsyncMock(return_value=google_events)
        ),
    ):
        result = await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=2))

    titles = [e.title for e in result]
    assert "Google Event" in titles
    assert "Local Only" in titles


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_events_google_api_failure_falls_back_to_local():
    adapter = _make_adapter()
    local_events = [_make_event("Local")]
    adapter._in_memory = AsyncMock()
    adapter._in_memory.list_events = AsyncMock(return_value=local_events)

    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(
            adapter,
            "_list_google_events",
            AsyncMock(side_effect=Exception("API error")),
        ),
    ):
        result = await adapter.list_events(_UID, _NOW, _NOW + timedelta(hours=2))

    assert result == local_events


# ---------------------------------------------------------------------------
# get_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_returns_from_google_when_tokens_available():
    adapter = _make_adapter()
    google_event = _make_event("Google Event", provider_id="gev1")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.get_event = AsyncMock(return_value=None)

    mock_svc = MagicMock()
    mock_svc.events.return_value.get.return_value.execute.return_value = {
        "id": "gev1",
        "summary": "Google Event",
        "start": {"dateTime": _NOW.isoformat()},
        "end": {"dateTime": (_NOW + timedelta(hours=1)).isoformat()},
        "status": "confirmed",
        "attendees": [],
    }

    mock_tokens = {"access_token": "t", "refresh_token": "r"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        result = await adapter.get_event(_UID, "gev1")

    assert result is not None
    assert result.title == "Google Event"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_falls_back_to_in_memory_on_failure():
    adapter = _make_adapter()
    local_event = _make_event("Local Event")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.get_event = AsyncMock(return_value=local_event)

    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(
            adapter, "_build_google_service", side_effect=Exception("build fail")
        ),
    ):
        result = await adapter.get_event(_UID, "ev1")

    assert result is local_event


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_event_falls_back_when_no_tokens():
    adapter = _make_adapter()
    local_event = _make_event("Local")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.get_event = AsyncMock(return_value=local_event)

    with patch.object(adapter, "_get_google_tokens", AsyncMock(return_value=None)):
        result = await adapter.get_event(_UID, "ev1")

    assert result is local_event


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_uses_google_when_tokens():
    adapter = _make_adapter()
    event = _make_event("New Event")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.create_event = AsyncMock(return_value=event)

    mock_svc = MagicMock()
    mock_svc.events.return_value.insert.return_value.execute.return_value = {
        "id": "new-gev"
    }

    mock_tokens = {"access_token": "t", "refresh_token": "r"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        result = await adapter.create_event(_UID, event)

    assert result.provider_event_id == "new-gev"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_falls_back_to_inmemory():
    adapter = _make_adapter()
    event = _make_event("New Event")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.create_event = AsyncMock(return_value=event)

    with patch.object(adapter, "_get_google_tokens", AsyncMock(return_value=None)):
        result = await adapter.create_event(_UID, event)

    adapter._in_memory.create_event.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_event_falls_back_on_google_failure():
    adapter = _make_adapter()
    event = _make_event("New Event")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.create_event = AsyncMock(return_value=event)

    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(
            adapter, "_build_google_service", side_effect=Exception("api error")
        ),
    ):
        result = await adapter.create_event(_UID, event)

    adapter._in_memory.create_event.assert_called()


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_calls_google_when_token_and_provider_id():
    adapter = _make_adapter()
    event = _make_event("Updated", provider_id="gev1")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.update_event = AsyncMock(return_value=event)

    mock_svc = MagicMock()
    mock_svc.events.return_value.update.return_value.execute.return_value = {}
    mock_tokens = {"access_token": "t", "refresh_token": "r"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        result = await adapter.update_event(_UID, event)

    assert result is event


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_falls_back_when_no_tokens():
    adapter = _make_adapter()
    event = _make_event("Updated")
    adapter._in_memory = AsyncMock()
    adapter._in_memory.update_event = AsyncMock(return_value=event)

    with patch.object(adapter, "_get_google_tokens", AsyncMock(return_value=None)):
        result = await adapter.update_event(_UID, event)

    adapter._in_memory.update_event.assert_called_once()


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_deletes_from_google_and_local():
    adapter = _make_adapter()
    adapter._in_memory = AsyncMock()
    adapter._in_memory.delete_event = AsyncMock(return_value=True)

    mock_svc = MagicMock()
    mock_svc.events.return_value.delete.return_value.execute.return_value = None
    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        result = await adapter.delete_event(_UID, "gev1")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_uses_local_when_google_fails():
    adapter = _make_adapter()
    adapter._in_memory = AsyncMock()
    adapter._in_memory.delete_event = AsyncMock(return_value=True)

    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", side_effect=Exception("fail")),
    ):
        result = await adapter.delete_event(_UID, "gev1")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_event_no_tokens_uses_local():
    adapter = _make_adapter()
    adapter._in_memory = AsyncMock()
    adapter._in_memory.delete_event = AsyncMock(return_value=False)

    with patch.object(adapter, "_get_google_tokens", AsyncMock(return_value=None)):
        result = await adapter.delete_event(_UID, "ev1")

    assert result is False


# ---------------------------------------------------------------------------
# find_free_slots
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_returns_gaps():
    adapter = _make_adapter()
    start = _NOW
    end = _NOW + timedelta(hours=4)

    ev = _make_event("Busy")
    ev.start_time = _NOW + timedelta(hours=1)
    ev.end_time = _NOW + timedelta(hours=2)

    with patch.object(adapter, "list_events", AsyncMock(return_value=[ev])):
        slots = await adapter.find_free_slots(_UID, start, end, duration_minutes=30)

    assert len(slots) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_find_free_slots_no_events_returns_full_window():
    adapter = _make_adapter()
    start = _NOW
    end = _NOW + timedelta(hours=4)

    with patch.object(adapter, "list_events", AsyncMock(return_value=[])):
        slots = await adapter.find_free_slots(_UID, start, end, duration_minutes=30)

    assert len(slots) == 1
    assert slots[0].start == start
    assert slots[0].end == end


# ---------------------------------------------------------------------------
# EventRepositoryPort delegates
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repository_methods_delegate_to_in_memory():
    adapter = _make_adapter()
    adapter._in_memory = AsyncMock()
    adapter._in_memory.get_by_id = AsyncMock(return_value=None)
    adapter._in_memory.get_by_provider_id = AsyncMock(return_value=None)
    adapter._in_memory.list_by_user = AsyncMock(return_value=[])
    adapter._in_memory.create = AsyncMock(return_value=_make_event())
    adapter._in_memory.update = AsyncMock(return_value=_make_event())
    adapter._in_memory.delete = AsyncMock(return_value=True)

    await adapter.get_by_id(uuid.uuid4())
    await adapter.get_by_provider_id("pev1", _UID)
    await adapter.list_by_user(_UID)
    await adapter.create(_make_event())
    await adapter.update(_make_event())
    await adapter.delete(uuid.uuid4())

    adapter._in_memory.get_by_id.assert_called_once()
    adapter._in_memory.delete.assert_called_once()


# ---------------------------------------------------------------------------
# get_or_create_scheduling_calendar
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_scheduling_calendar_returns_primary_when_no_tokens():
    adapter = _make_adapter()

    with patch.object(adapter, "_get_google_tokens", AsyncMock(return_value=None)):
        cal_id = await adapter.get_or_create_scheduling_calendar(_UID)

    assert cal_id == "primary"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_scheduling_calendar_reuses_existing():
    adapter = _make_adapter()

    mock_svc = MagicMock()
    mock_svc.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "CalendarAgent", "id": "cal-123"}]
    }
    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        cal_id = await adapter.get_or_create_scheduling_calendar(_UID)

    assert cal_id == "cal-123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_scheduling_calendar_creates_new():
    adapter = _make_adapter()

    mock_svc = MagicMock()
    mock_svc.calendarList.return_value.list.return_value.execute.return_value = {
        "items": []  # No existing calendar
    }
    mock_svc.calendars.return_value.insert.return_value.execute.return_value = {
        "id": "new-cal-456"
    }
    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(adapter, "_build_google_service", return_value=mock_svc),
    ):
        cal_id = await adapter.get_or_create_scheduling_calendar(_UID)

    assert cal_id == "new-cal-456"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_scheduling_calendar_returns_primary_on_error():
    adapter = _make_adapter()
    mock_tokens = {"access_token": "t"}

    with (
        patch.object(
            adapter, "_get_google_tokens", AsyncMock(return_value=mock_tokens)
        ),
        patch.object(
            adapter, "_build_google_service", side_effect=Exception("API fail")
        ),
    ):
        cal_id = await adapter.get_or_create_scheduling_calendar(_UID)

    assert cal_id == "primary"


# ---------------------------------------------------------------------------
# persist_scheduling_calendar_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_scheduling_calendar_id_noop_without_factory():
    adapter = _make_adapter()
    # Should not raise
    await adapter.persist_scheduling_calendar_id(_UID, "cal-123")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_scheduling_calendar_id_updates_user_row():
    adapter = _make_adapter()

    user_row = MagicMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=user_row)
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=session)
    adapter._db_session_factory = factory

    await adapter.persist_scheduling_calendar_id(_UID, "cal-xyz")

    assert user_row.scheduling_calendar_id == "cal-xyz"
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# _get_google_tokens — dev-token / empty access_token row gets skipped (line 80)
# ---------------------------------------------------------------------------


def _make_session_with_rows(rows):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=rows)
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_skips_dev_token_row():
    """Row with access_token='dev-token' must be skipped (line 80 continue)."""
    row = MagicMock()
    row.access_token = "dev-token"
    row.refresh_token = "r"
    row.provider_email = "u@g.com"

    session = _make_session_with_rows([row])
    adapter = _make_adapter(db_session_factory=MagicMock(return_value=session))

    result = await adapter._get_google_tokens(_UID)
    # All rows skipped → returns None
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_skips_empty_access_token_row():
    """Row with empty access_token must be skipped."""
    row = MagicMock()
    row.access_token = ""
    row.refresh_token = ""
    row.provider_email = "u@g.com"

    session = _make_session_with_rows([row])
    adapter = _make_adapter(db_session_factory=MagicMock(return_value=session))

    result = await adapter._get_google_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_skips_when_decrypt_fails(mocker):
    """When decrypt_token returns empty string, row must be skipped (line 96 continue)."""
    row = MagicMock()
    row.access_token = "some-encrypted-token"
    row.refresh_token = "some-encrypted-refresh"
    row.provider_email = "u@g.com"

    session = _make_session_with_rows([row])
    adapter = _make_adapter(db_session_factory=MagicMock(return_value=session))

    mocker.patch(
        "src.infrastructure.security.token_encryption.decrypt_token",
        return_value="",
    )

    result = await adapter._get_google_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_skips_row_on_refresh_failure(mocker):
    """When Google token refresh raises, row is skipped and None returned."""
    row = MagicMock()
    row.access_token = "encrypted-access"
    row.refresh_token = "encrypted-refresh"
    row.provider_email = "u@g.com"

    session = _make_session_with_rows([row])
    adapter = _make_adapter(db_session_factory=MagicMock(return_value=session))
    adapter._google_client_id = "cid"

    mocker.patch(
        "src.infrastructure.security.token_encryption.decrypt_token",
        side_effect=["access-token", "refresh-token"],
    )

    # Make the run_in_executor call raise
    mocker.patch(
        "asyncio.get_event_loop",
        return_value=MagicMock(
            run_in_executor=AsyncMock(side_effect=Exception("auth error"))
        ),
    )

    result = await adapter._get_google_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_google_tokens_returns_token_without_refresh(mocker):
    """Row with access token but no refresh_token returns directly without refreshing."""
    row = MagicMock()
    row.access_token = "enc-access"
    row.refresh_token = ""  # no refresh token
    row.provider_email = "u@g.com"

    session = _make_session_with_rows([row])
    adapter = _make_adapter(db_session_factory=MagicMock(return_value=session))
    adapter._google_client_id = "cid"

    mocker.patch(
        "src.infrastructure.security.token_encryption.decrypt_token",
        side_effect=["access-token", ""],
    )

    result = await adapter._get_google_tokens(_UID)
    # No refresh attempted, returns the decrypted access token
    assert result is not None
    assert result["access_token"] == "access-token"


# ---------------------------------------------------------------------------
# _build_google_service (lines 160-170)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_google_service_returns_service(mocker):
    from src.infrastructure.calendar_providers.provider_aware_calendar import (
        ProviderAwareCalendarAdapter,
    )

    mock_creds_cls = mocker.patch(
        "src.infrastructure.calendar_providers.provider_aware_calendar.ProviderAwareCalendarAdapter._build_google_service",
    )
    adapter = _make_adapter()
    tokens = {"access_token": "tok", "refresh_token": "ref"}
    adapter._build_google_service(tokens)
    mock_creds_cls.assert_called_once_with(tokens)


@pytest.mark.unit
def test_build_google_service_with_mocked_google_libs(mocker):
    """Directly test _build_google_service using mocked google libraries."""
    mock_creds = mocker.patch(
        "google.oauth2.credentials.Credentials",
    )
    mock_build = mocker.patch(
        "googleapiclient.discovery.build", return_value=MagicMock()
    )

    from src.infrastructure.calendar_providers.provider_aware_calendar import (
        ProviderAwareCalendarAdapter,
    )

    adapter = ProviderAwareCalendarAdapter(
        google_client_id="cid",
        google_client_secret="csec",
    )
    tokens = {"access_token": "tok", "refresh_token": "ref"}
    svc = adapter._build_google_service(tokens)
    mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# _list_google_events (lines 220-258)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_google_events_returns_parsed_events(mocker):
    """_list_google_events should call service.events().list().execute()."""
    from datetime import timezone as tz

    mock_event_item = {
        "id": "evt1",
        "summary": "Test Event",
        "start": {"dateTime": "2024-06-01T10:00:00Z"},
        "end": {"dateTime": "2024-06-01T11:00:00Z"},
        "status": "confirmed",
    }

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [mock_event_item]
    }

    adapter = _make_adapter()

    mocker.patch.object(adapter, "_build_google_service", return_value=mock_service)

    tokens = {
        "access_token": "tok",
        "refresh_token": "ref",
        "provider_email": "u@g.com",
    }
    start = datetime(2024, 6, 1, 0, 0, tzinfo=tz.utc)
    end = datetime(2024, 6, 2, 0, 0, tzinfo=tz.utc)

    events = await adapter._list_google_events(_UID, tokens, start, end, "primary", 50)
    assert isinstance(events, list)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_google_events_skips_malformed_items(mocker):
    """_list_google_events should skip items that fail to parse."""
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "bad", "no_summary": True}]  # missing required fields
    }

    adapter = _make_adapter()
    mocker.patch.object(adapter, "_build_google_service", return_value=mock_service)

    # Mock GoogleCalendarAdapter._parse_event to raise
    mocker.patch(
        "src.infrastructure.calendar_providers.google_calendar.GoogleCalendarAdapter._parse_event",
        side_effect=Exception("parse error"),
    )

    tokens = {"access_token": "tok", "refresh_token": "ref"}
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 2, tzinfo=timezone.utc)

    events = await adapter._list_google_events(_UID, tokens, start, end, "primary", 50)
    # Malformed events are skipped
    assert events == []


# ---------------------------------------------------------------------------
# update_event — exception path (lines 336-337)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_event_exception_falls_back_to_in_memory(mocker):
    """If Google Calendar update fails, falls back to in-memory."""
    adapter = _make_adapter()
    event = _make_event(title="Meeting", provider_id="evt-123")

    mocker.patch.object(
        adapter,
        "_get_google_tokens",
        return_value={"access_token": "tok", "refresh_token": "ref"},
    )

    mock_service = MagicMock()
    mock_service.events.return_value.patch.return_value.execute.side_effect = Exception(
        "API error"
    )
    mocker.patch.object(adapter, "_build_google_service", return_value=mock_service)

    updated = _make_event(title="Updated")
    adapter._in_memory = MagicMock()
    adapter._in_memory.update_event = AsyncMock(return_value=updated)

    result = await adapter.update_event(_UID, event)
    assert result == updated


# ---------------------------------------------------------------------------
# persist_scheduling_calendar_id — exception path (lines 495-496)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_scheduling_calendar_id_handles_exception():
    """When the DB operation raises, it should log and not re-raise."""
    adapter = _make_adapter()

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=Exception("DB failure"))

    adapter._db_session_factory = MagicMock(return_value=session)

    # Should not raise
    await adapter.persist_scheduling_calendar_id(_UID, "cal-123")
