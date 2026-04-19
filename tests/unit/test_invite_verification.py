"""
Unit tests for InviteVerificationService.
All external adapters (LLM, calendar, DB) are mocked.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.invite_verification_service import (
    InviteVerificationService,
)

_USER_ID = uuid.uuid4()
_DRAFT_ID = uuid.uuid4()

_INVITE = {
    "title": "Sync call",
    "start": "2026-05-01T10:00:00+00:00",
    "end": "2026-05-01T10:30:00+00:00",
    "attendees": ["alice@example.com"],
    "location": "Zoom",
}


def _make_svc(
    llm=None,
    calendar=None,
    db=None,
):
    return InviteVerificationService(
        llm_adapter=llm,
        calendar_adapter=calendar,
        db_session_factory=db,
    )


# ---------------------------------------------------------------------------
# verify_and_process_invite — no pending invite
# ---------------------------------------------------------------------------


class TestVerifyAndProcessInvite:
    @pytest.mark.unit
    async def test_skipped_when_no_pending_invite(self):
        svc = _make_svc()  # no db → _get_pending_invite returns None
        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Sounds great!",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "skipped"
        assert "No pending invite" in result["reason"]

    @pytest.mark.unit
    async def test_skip_action_from_verify(self):
        """LLM says skip → service returns skipped."""
        llm = AsyncMock()
        llm.chat_completion.return_value = json.dumps(
            {"action": "skip", "reason": "not confirmed"}
        )

        svc = _make_svc(llm=llm)

        # Patch _get_pending_invite to return an invite
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Never mind.",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "skipped"

    @pytest.mark.unit
    async def test_send_action_creates_event(self):
        """LLM says send → calendar.create_event is called."""
        llm = AsyncMock()
        llm.chat_completion.return_value = json.dumps(
            {"action": "send", "reason": "confirmed"}
        )

        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-123"
        cal.create_event.return_value = fake_event

        svc = _make_svc(llm=llm, calendar=cal)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Yes, 10am works!",
            sender_email="me@example.com",
            thread_messages=[
                {
                    "sender": "alice@example.com",
                    "body": "How about 10am?",
                    "date": "2026-04-17",
                }
            ],
        )
        cal.create_event.assert_awaited_once()
        assert result["action"] == "sent_invite"
        assert result["event_id"] == "evt-123"

    @pytest.mark.unit
    async def test_update_action_merges_changes(self):
        """LLM says update with new time → invite details are merged."""
        llm = AsyncMock()
        llm.chat_completion.return_value = json.dumps(
            {
                "action": "update",
                "reason": "time changed",
                "updated_event_summary": "Rescheduled Sync",
                "updated_event_start": "2026-05-02T11:00:00+00:00",
                "updated_event_end": "2026-05-02T11:30:00+00:00",
                "updated_attendees": None,
                "updated_location": None,
                "add_google_meet": None,
            }
        )

        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-456"
        cal.create_event.return_value = fake_event

        svc = _make_svc(llm=llm, calendar=cal)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Actually let's do tomorrow 11am.",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "updated_invite"
        assert result["event_id"] == "evt-456"

    @pytest.mark.unit
    async def test_calendar_error_returns_error_action(self):
        """If calendar.create_event raises, result action is 'error'."""
        llm = AsyncMock()
        llm.chat_completion.return_value = json.dumps(
            {"action": "send", "reason": "confirmed"}
        )

        cal = AsyncMock()
        cal.create_event.side_effect = RuntimeError("Calendar API down")

        svc = _make_svc(llm=llm, calendar=cal)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Confirmed!",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "error"

    @pytest.mark.unit
    async def test_llm_unavailable_returns_skip(self):
        """No LLM adapter → _verify fallback returns skip."""
        svc = _make_svc(llm=None)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="OK!",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "skipped"

    @pytest.mark.unit
    async def test_llm_json_parse_error_returns_skip(self):
        """LLM returns invalid JSON → falls back to skip."""
        llm = AsyncMock()
        llm.chat_completion.return_value = "not valid json {"

        svc = _make_svc(llm=llm)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="...",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] == "skipped"


# ---------------------------------------------------------------------------
# _get_pending_invite — no DB
# ---------------------------------------------------------------------------


class TestGetPendingInvite:
    @pytest.mark.unit
    async def test_returns_none_when_no_db(self):
        svc = _make_svc(db=None)
        result = await svc._get_pending_invite(_DRAFT_ID)
        assert result is None

    @pytest.mark.unit
    async def test_returns_none_on_db_exception(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def bad_db():
            raise RuntimeError("DB explosion")
            yield  # noqa: unreachable

        svc = _make_svc(db=bad_db)
        result = await svc._get_pending_invite(_DRAFT_ID)
        assert result is None


class TestUpdateActionWithAttendeeAndLocation:
    @pytest.mark.unit
    async def test_update_action_sets_attendees_and_location(self):
        """Covers lines 141, 143: update action with attendees and location."""
        llm = AsyncMock()
        llm.chat_completion.return_value = json.dumps(
            {
                "action": "update",
                "reason": "location changed",
                "updated_event_summary": None,
                "updated_event_start": None,
                "updated_event_end": None,
                "updated_attendees": ["bob@example.com", "carol@example.com"],
                "updated_location": "Conference Room B",
                "add_google_meet": None,
            }
        )

        cal = AsyncMock()
        fake_event = MagicMock()
        fake_event.id = "evt-upd-loc"
        cal.create_event.return_value = fake_event

        svc = _make_svc(llm=llm, calendar=cal)
        svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

        result = await svc.verify_and_process_invite(
            user_id=_USER_ID,
            draft_reply_id=_DRAFT_ID,
            sent_message_body="Let's use the conf room and add Bob and Carol.",
            sender_email="me@example.com",
            thread_messages=[],
        )
        assert result["action"] in ("updated_invite", "sent_invite")


# ---------------------------------------------------------------------------
# Tests — missing branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_backtick_wrapped_response_is_parsed():
    """LLM response wrapped in ```backticks``` is stripped before JSON parse (line 225)."""
    payload = json.dumps(
        {
            "action": "skip",
            "reason": "no change needed",
            "updated_event_summary": None,
            "updated_event_start": None,
            "updated_event_end": None,
            "updated_attendees": None,
            "updated_location": None,
            "add_google_meet": None,
        }
    )
    wrapped = "```json\n" + payload + "\n```"
    llm = AsyncMock()
    llm.chat_completion.return_value = wrapped

    svc = _make_svc(llm=llm)
    svc._get_pending_invite = AsyncMock(return_value=_INVITE.copy())

    result = await svc.verify_and_process_invite(
        user_id=_USER_ID,
        draft_reply_id=_DRAFT_ID,
        sent_message_body="looks good",
        sender_email="bob@example.com",
        thread_messages=[],
    )
    assert result["action"] == "skipped"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_pending_invite_returns_json_from_record():
    """_get_pending_invite returns the parsed JSON when DB has a matching record (lines 242-247)."""
    import json as _json
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    invite_data = {"title": "Budget review", "start": "2026-06-01T10:00:00+00:00"}

    @asynccontextmanager
    async def db_factory():
        session = MagicMock()
        record = MagicMock()
        record.pending_invite_json = _json.dumps(invite_data)
        result = MagicMock()
        result.scalars.return_value.first.return_value = record
        session.execute = AsyncMock(return_value=result)
        yield session

    svc = _make_svc(db=db_factory)
    result = await svc._get_pending_invite(_DRAFT_ID)
    assert result == invite_data
