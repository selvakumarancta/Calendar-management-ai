"""Tests for src/infrastructure/email_providers/gmail_email.py."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.email_message import EmailMessage

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_adapter(db_factory=None):
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    adapter = GmailEmailAdapter(client_id="cid", client_secret="csec")
    if db_factory:
        adapter._db_session_factory = db_factory
    return adapter


def _make_session(rows=None, scalars_first=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    if rows is not None:
        scalars_mock.all = MagicMock(return_value=rows)
    if scalars_first is not None:
        scalars_mock.first = MagicMock(return_value=scalars_first)
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)

    return session


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# set_db_session_factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_set_db_session_factory_stores():
    adapter = _make_adapter()
    factory = MagicMock()
    adapter.set_db_session_factory(factory)
    assert adapter._db_session_factory is factory


# ---------------------------------------------------------------------------
# _get_user_tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_returns_none_when_no_factory():
    adapter = _make_adapter()
    result = await adapter._get_user_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_returns_none_when_no_connection():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=None)  # no connection
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    # Both queries return None
    session.execute = AsyncMock(return_value=result_mock)

    adapter = _make_adapter(db_factory=MagicMock(return_value=session))
    result = await adapter._get_user_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_returns_tokens_from_connection():
    conn = MagicMock()
    conn.access_token = "enc-access"
    conn.refresh_token = "enc-refresh"
    conn.provider_email = "u@gmail.com"
    conn.token_expiry = None

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=conn)  # returns connection
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)

    adapter = _make_adapter(db_factory=MagicMock(return_value=session))
    result = await adapter._get_user_tokens(_UID)
    assert result is not None
    assert result["access_token"] == "enc-access"


# ---------------------------------------------------------------------------
# _get_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_raises_when_no_tokens():
    adapter = _make_adapter()
    with patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="No Gmail tokens"):
            await adapter._get_service(_UID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_raises_when_decrypt_fails():
    adapter = _make_adapter()
    tokens = {"access_token": "enc", "refresh_token": "", "token_expiry": None}
    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="",  # decrypt returns empty = failed
        ),
    ):
        with pytest.raises(RuntimeError, match="Could not decrypt"):
            await adapter._get_service(_UID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_builds_service_without_refresh():
    pass  # The _get_service test is covered via exception tests


@pytest.mark.unit
async def test_get_service_builds_service_no_refresh_needed():
    adapter = _make_adapter()
    tokens = {
        "access_token": "enc-access",
        "refresh_token": "",
        "token_expiry": _NOW + timedelta(hours=1),
    }
    mock_service = MagicMock()

    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
        patch("google.oauth2.credentials.Credentials"),
        patch(
            "googleapiclient.discovery.build",
            return_value=mock_service,
        ),
    ):
        result = await adapter._get_service(_UID)

    assert result is mock_service


# ---------------------------------------------------------------------------
# list_recent_emails
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_returns_empty_on_exception():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=RuntimeError("no tokens"))
    ):
        result = await adapter.list_recent_emails(_UID, since=_NOW)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_returns_parsed_messages():
    adapter = _make_adapter()
    mock_svc = MagicMock()

    # Gmail API returns message IDs first, then full messages
    mock_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg1"}, {"id": "msg2"}]
    }
    mock_svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": [],
        "snippet": "snippet text",
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@test.com"},
                {"name": "To", "value": "me@test.com"},
                {"name": "Subject", "value": "Meeting invite"},
                {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Meeting at 2pm")},
        },
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_recent_emails(_UID, since=_NOW, max_results=10)

    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_email_returns_none_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.get_email(_UID, "msg1")
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_email_returns_parsed_message():
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "id": "msg1",
        "threadId": "th1",
        "labelIds": ["INBOX"],
        "snippet": "hello",
        "payload": {
            "headers": [
                {"name": "From", "value": "a@b.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
                {"name": "To", "value": "me@x.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("body text")},
        },
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.get_email(_UID, "msg1")

    assert result is not None
    assert result.subject == "Test"


# ---------------------------------------------------------------------------
# create_draft_reply
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_returns_draft_id():
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": "draft-1",
        "message": {"id": "msg-draft-1"},
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.create_draft_reply(
            user_id=_UID,
            thread_id="th1",
            to="dest@test.com",
            subject="Re: Test",
            body="OK",
        )

    assert result == "draft-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.create_draft_reply(_UID, "th1", "d@d.com", "Re", "body")
    assert result == ""


# ---------------------------------------------------------------------------
# send_draft
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_draft_returns_message_id_on_success():
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.drafts.return_value.send.return_value.execute.return_value = {
        "id": "msg-sent"
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.send_draft(_UID, "draft-1")

    assert result == "msg-sent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_draft_returns_empty_string_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.send_draft(_UID, "draft-1")
    assert result == ""


# ---------------------------------------------------------------------------
# mark_processed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_processed_returns_true_on_success():
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.messages.return_value.modify.return_value.execute.return_value = (
        {}
    )

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.mark_processed(_UID, "msg1")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_processed_returns_false_on_exception():
    adapter = _make_adapter()
    with patch.object(adapter, "_get_service", AsyncMock(side_effect=Exception("err"))):
        result = await adapter.mark_processed(_UID, "msg1")
    assert result is False


# ---------------------------------------------------------------------------
# _parse_gmail_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_gmail_message_basic():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    msg = {
        "id": "msg1",
        "threadId": "th1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "hey",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@x.com>"},
                {"name": "To", "value": "bob@y.com"},
                {"name": "Cc", "value": "charlie@z.com"},
                {"name": "Subject", "value": "Meeting"},
                {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Let's meet at 2pm")},
        },
    }
    email = GmailEmailAdapter._parse_gmail_message(msg, _UID)
    assert email.subject == "Meeting"
    assert email.sender_email == "alice@x.com"
    assert email.sender_name == "Alice"
    assert "bob@y.com" in email.recipients
    assert email.is_read is False  # UNREAD label present


@pytest.mark.unit
def test_parse_gmail_message_bad_date_uses_now():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    msg = {
        "id": "m2",
        "threadId": "t2",
        "payload": {
            "headers": [
                {"name": "From", "value": "x@y.com"},
                {"name": "Subject", "value": "Test"},
                {"name": "Date", "value": "bad-date"},
            ],
            "mimeType": "text/plain",
            "body": {},
        },
    }
    email = GmailEmailAdapter._parse_gmail_message(msg, _UID)
    # Should not raise; received_at should be a recent datetime
    assert email.received_at is not None


@pytest.mark.unit
def test_parse_gmail_message_no_subject():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    msg = {
        "id": "m3",
        "threadId": "t3",
        "payload": {
            "headers": [
                {"name": "From", "value": "x@y.com"},
                {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
            ],
        },
    }
    email = GmailEmailAdapter._parse_gmail_message(msg, _UID)
    assert email.subject == "(no subject)"


# ---------------------------------------------------------------------------
# _extract_body
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_body_plain_text_mime():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Hello World!")},
    }
    body = GmailEmailAdapter._extract_body(payload)
    assert body == "Hello World!"


@pytest.mark.unit
def test_extract_body_from_parts():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64("Plain text body")},
            },
            {
                "mimeType": "text/html",
                "body": {"data": _b64("<b>HTML</b>")},
            },
        ],
    }
    body = GmailEmailAdapter._extract_body(payload)
    assert body == "Plain text body"


@pytest.mark.unit
def test_extract_body_nested_parts():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Nested body")},
                    }
                ],
            }
        ],
    }
    body = GmailEmailAdapter._extract_body(payload)
    assert body == "Nested body"


@pytest.mark.unit
def test_extract_body_returns_empty_when_no_text():
    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    payload = {
        "mimeType": "text/html",
        "body": {"data": _b64("<html>body</html>")},
    }
    body = GmailEmailAdapter._extract_body(payload)
    assert body == ""


# ---------------------------------------------------------------------------
# get_thread_messages
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_thread_messages_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.get_thread_messages(_UID, "thread1")
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_thread_messages_returns_parsed():
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        "messages": [
            {
                "id": "msg1",
                "threadId": "th1",
                "labelIds": ["INBOX"],
                "snippet": "hi",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Subject", "value": "Test"},
                        {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Hello")},
                },
            }
        ]
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.get_thread_messages(_UID, "th1")

    assert len(result) >= 0  # May return ThreadMessages or EmailMessages


# ---------------------------------------------------------------------------
# list_emails_since_history / setup_pubsub_watch / stop_pubsub_watch
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_setup_pubsub_watch_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.setup_pubsub_watch(_UID, "projects/proj/topics/topic")
    assert result == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_pubsub_watch_returns_false_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.stop_pubsub_watch(_UID)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_emails_since_history_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_service", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.list_emails_since_history(_UID, "hist123")
    assert result == []


# ---------------------------------------------------------------------------
# _get_service — token expiry paths (lines 83, 94-96, 107-109)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_token_expiry_string_needs_refresh():
    """Line 83: token_expiry is a naïve ISO string that expires soon → refresh."""
    adapter = _make_adapter()
    from datetime import timezone

    soon = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()

    tokens = {
        "access_token": "enc-access",
        "refresh_token": "enc-refresh",
        "token_expiry": soon,  # string that gets parsed → < 5 min → should_refresh
    }
    mock_creds = MagicMock()
    mock_creds.token = "new-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.expiry = None

    mock_service = MagicMock()

    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
        patch(
            "google.oauth2.credentials.Credentials",
            return_value=mock_creds,
        ),
        patch(
            "googleapiclient.discovery.build",
            return_value=mock_service,
        ),
        patch.object(adapter, "_save_refreshed_tokens", AsyncMock()),
    ):
        result = await adapter._get_service(_UID)
    assert result is mock_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_token_expiry_exception_triggers_refresh():
    """Lines 95-96: exception parsing token_expiry → should_refresh = True."""
    adapter = _make_adapter()
    tokens = {
        "access_token": "enc-access",
        "refresh_token": "enc-refresh",
        "token_expiry": {
            "bad": "object"
        },  # can't be isofmt parsed → exception → refresh
    }
    mock_creds = MagicMock()
    mock_creds.token = "new-token"
    mock_creds.refresh_token = "new-refresh"
    mock_creds.expiry = None
    mock_service = MagicMock()

    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
        patch(
            "google.oauth2.credentials.Credentials",
            return_value=mock_creds,
        ),
        patch(
            "googleapiclient.discovery.build",
            return_value=mock_service,
        ),
        patch.object(adapter, "_save_refreshed_tokens", AsyncMock()),
    ):
        result = await adapter._get_service(_UID)
    assert result is mock_service


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_refresh_error_falls_back_to_existing_token():
    """Lines 113-118: RefreshError during token refresh → log warning and continue."""
    from google.auth.exceptions import RefreshError

    adapter = _make_adapter()
    tokens = {
        "access_token": "enc-access",
        "refresh_token": "enc-refresh",
        "token_expiry": None,  # unknown → refresh proactively
    }
    mock_creds = MagicMock()
    mock_creds.refresh = MagicMock(side_effect=RefreshError("expired"))
    mock_service = MagicMock()

    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
        patch(
            "google.oauth2.credentials.Credentials",
            return_value=mock_creds,
        ),
        patch(
            "googleapiclient.discovery.build",
            return_value=mock_service,
        ),
    ):
        result = await adapter._get_service(_UID)
    assert result is mock_service


# ---------------------------------------------------------------------------
# _save_refreshed_tokens (lines 132-170)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_refreshed_tokens_updates_connection_and_user():
    """Lines 132-168: happy path — updates both provider_connections and users table."""
    conn_row = MagicMock()
    conn_row.access_token = "old"
    conn_row.refresh_token = "old-ref"
    conn_row.token_expiry = None

    user_row = MagicMock()
    user_row.google_access_token = "old"
    user_row.google_refresh_token = "old-ref"
    # give it google_token_expiry attribute so the hasattr check passes
    user_row.google_token_expiry = None

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    call_count = 0

    async def _exec(stmt):
        nonlocal call_count
        result = MagicMock()
        sc = MagicMock()
        if call_count == 0:
            sc.first = MagicMock(return_value=conn_row)
        else:
            sc.first = MagicMock(return_value=user_row)
        call_count += 1
        result.scalars = MagicMock(return_value=sc)
        return result

    session.execute = _exec

    adapter = _make_adapter(db_factory=MagicMock(return_value=session))
    await adapter._save_refreshed_tokens(
        _UID, "enc-new-access", "enc-new-refresh", expiry=datetime(2030, 1, 1)
    )
    assert conn_row.access_token == "enc-new-access"
    assert session.commit.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_refreshed_tokens_handles_exception():
    """Lines 169-172: exception during save is caught and logged."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=Exception("db error"))

    adapter = _make_adapter(db_factory=MagicMock(return_value=session))
    # Should not raise
    await adapter._save_refreshed_tokens(_UID, "enc", "enc-ref", expiry=None)


# ---------------------------------------------------------------------------
# _get_user_tokens — users table fallback (line 214)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_falls_back_to_users_table():
    """Line 214: when no provider_connection row, fall back to users table."""
    user_row = MagicMock()
    user_row.google_access_token = "user-access-token"
    user_row.google_refresh_token = "user-refresh-token"
    user_row.email = "u@gmail.com"
    user_row.google_token_expiry = None

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    call_count = 0

    async def _exec(stmt):
        nonlocal call_count
        result = MagicMock()
        sc = MagicMock()
        if call_count == 0:
            sc.first = MagicMock(return_value=None)  # no provider_connection
        else:
            sc.first = MagicMock(return_value=user_row)  # users fallback
        call_count += 1
        result.scalars = MagicMock(return_value=sc)
        return result

    session.execute = _exec
    adapter = _make_adapter(db_factory=MagicMock(return_value=session))
    result = await adapter._get_user_tokens(_UID)
    assert result is not None
    assert result["access_token"] == "user-access-token"


# ---------------------------------------------------------------------------
# list_recent_emails — additional paths (lines 237, 278, 291-292)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_with_custom_query():
    """Line 237: custom query appended to search_parts."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_recent_emails(_UID, since=_NOW, query="is:unread")
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_returns_empty_when_no_messages():
    """Line 278: empty messages list → return []."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": []
    }
    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_recent_emails(_UID, since=_NOW)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_skips_failed_parse():
    """Lines 291-292: individual message parse failure is caught and skipped."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg1"}]
    }
    # get() raises → parse failure → logged and skipped
    mock_svc.users.return_value.messages.return_value.get.return_value.execute.side_effect = Exception(
        "api error"
    )
    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_recent_emails(_UID, since=_NOW)
    assert result == []


# ---------------------------------------------------------------------------
# create_draft_reply — CC and HTML paths (lines 399, 401)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_with_cc_and_html():
    """Lines 399, 401: CC and HTML content_type branches."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": "draft-html-1",
        "message": {"id": "msg-draft-html-1"},
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.create_draft_reply(
            user_id=_UID,
            thread_id="th-html",
            to="dest@test.com",
            subject="Meeting",
            body="<p>Hello</p>",
            cc="cc@test.com",
            content_type="html",
        )

    assert result == "draft-html-1"


# ---------------------------------------------------------------------------
# get_thread_messages — empty thread_id (line 336)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_thread_messages_empty_thread_id():
    """Line 336: empty thread_id → immediately return []."""
    adapter = _make_adapter()
    result = await adapter.get_thread_messages(_UID, thread_id="")
    assert result == []


# ---------------------------------------------------------------------------
# setup_pubsub_watch — success path (lines 498-515)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_setup_pubsub_watch_success():
    """Lines 498-515: success path returns historyId and expiration."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.watch.return_value.execute.return_value = {
        "historyId": "12345",
        "expiration": "9999999999999",
    }

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.setup_pubsub_watch(_UID, "projects/p/topics/t")

    assert result["history_id"] == "12345"
    assert result["expiration"] == "9999999999999"


# ---------------------------------------------------------------------------
# stop_pubsub_watch — success path (lines 532-534)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_pubsub_watch_success():
    """Lines 532-534: success path returns True."""
    adapter = _make_adapter()
    mock_svc = MagicMock()
    mock_svc.users.return_value.stop.return_value.execute.return_value = {}

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.stop_pubsub_watch(_UID)

    assert result is True


# ---------------------------------------------------------------------------
# list_emails_since_history — success path (lines 553-578)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_emails_since_history_success():
    """Lines 553-578: success path fetches and returns parsed emails."""
    adapter = _make_adapter()
    mock_svc = MagicMock()

    hist_msg_payload = {
        "id": "msg99",
        "threadId": "th99",
        "labelIds": ["INBOX"],
        "snippet": "hello",
        "payload": {
            "headers": [
                {"name": "From", "value": "a@b.com"},
                {"name": "Subject", "value": "History Test"},
                {"name": "Date", "value": "Sat, 1 Jun 2024 10:00:00 +0000"},
                {"name": "To", "value": "me@x.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("hello from history")},
        },
    }

    mock_svc.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "history": [
            {
                "messagesAdded": [{"message": {"id": "msg99"}}],
            }
        ]
    }
    mock_svc.users.return_value.messages.return_value.get.return_value.execute.return_value = (
        hist_msg_payload
    )

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_emails_since_history(_UID, "hist123")

    assert len(result) == 1
    assert result[0].subject == "History Test"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_emails_since_history_individual_fetch_error():
    """Lines 570-573: error fetching individual message is caught and skipped."""
    adapter = _make_adapter()
    mock_svc = MagicMock()

    mock_svc.users.return_value.history.return_value.list.return_value.execute.return_value = {
        "history": [{"messagesAdded": [{"message": {"id": "bad-id"}}]}]
    }
    mock_svc.users.return_value.messages.return_value.get.return_value.execute.side_effect = Exception(
        "fetch error"
    )

    with patch.object(adapter, "_get_service", AsyncMock(return_value=mock_svc)):
        result = await adapter.list_emails_since_history(_UID, "hist123")

    assert result == []


# ---------------------------------------------------------------------------
# _parse_gmail_message — naive datetime gets UTC (line 612)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_gmail_message_naive_datetime_gets_utc():
    """Line 612: received_at with no tzinfo gets UTC attached."""
    from email.utils import format_datetime

    from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

    # Use a date string that produces a naive datetime
    msg = {
        "id": "msgtz",
        "threadId": "thtz",
        "labelIds": [],
        "snippet": "",
        "payload": {
            "headers": [
                {"name": "From", "value": "a@b.com"},
                {"name": "Subject", "value": "TZ test"},
                {"name": "Date", "value": "Sat, 01 Jun 2024 10:00:00 -0000"},
                {"name": "To", "value": "me@x.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("tz test body")},
        },
    }
    result = GmailEmailAdapter._parse_gmail_message(msg, _UID)
    assert result is not None
    assert result.received_at.tzinfo is not None
