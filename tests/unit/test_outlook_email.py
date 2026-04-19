"""Tests for src/infrastructure/email_providers/outlook_email.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.email_message import EmailMessage

_UID = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


def _make_adapter():
    from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

    return OutlookEmailAdapter()


def _make_session_first(return_value=None):
    """Session that returns `return_value` from scalars().first()."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=return_value)
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)
    return session


def _make_httpx_resp(status_code=200, json_data=None, raise_for_status=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    if raise_for_status:
        resp.raise_for_status = MagicMock(side_effect=raise_for_status)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _make_httpx_client(resp):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.patch = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# set_db_session_factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_set_db_session_factory():
    adapter = _make_adapter()
    factory = MagicMock()
    adapter.set_db_session_factory(factory)
    assert adapter._db_session_factory is factory


# ---------------------------------------------------------------------------
# _get_user_tokens — no factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_none_when_no_factory():
    adapter = _make_adapter()
    result = await adapter._get_user_tokens(_UID)
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_from_provider_connection():
    conn = MagicMock()
    conn.access_token = "enc-ms-access"
    conn.refresh_token = "enc-ms-refresh"
    conn.token_expiry = None
    conn.id = uuid.uuid4()

    session = _make_session_first(conn)
    adapter = _make_adapter()
    adapter._db_session_factory = MagicMock(return_value=session)

    result = await adapter._get_user_tokens(_UID)
    assert result is not None
    assert result["access_token"] == "enc-ms-access"
    assert result["source"] == "provider"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_user_tokens_falls_back_to_user_table():
    """No provider_connection → falls back to UserModel."""
    user_mock = MagicMock()
    user_mock.id = _UID
    user_mock.microsoft_access_token = "user-ms-token"
    user_mock.microsoft_refresh_token = "user-ms-refresh"
    user_mock.microsoft_token_expiry = None

    # First call (provider_connections) returns None
    # Second call (users table) returns the user
    results = [
        _make_scalar_result(None),
        _make_scalar_result(user_mock),
    ]
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=results)

    adapter = _make_adapter()
    adapter._db_session_factory = MagicMock(return_value=session)

    result = await adapter._get_user_tokens(_UID)
    assert result is not None
    assert result["source"] == "user"


def _make_scalar_result(first_value):
    result = MagicMock()
    s = MagicMock()
    s.first = MagicMock(return_value=first_value)
    result.scalars = MagicMock(return_value=s)
    return result


# ---------------------------------------------------------------------------
# _ms_refresh
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ms_refresh_raises_when_missing_creds():
    adapter = _make_adapter()
    with patch.dict(
        "os.environ", {"MICROSOFT_CLIENT_ID": "", "MICROSOFT_CLIENT_SECRET": ""}
    ):
        with pytest.raises(RuntimeError, match="Cannot refresh MS token"):
            await adapter._ms_refresh("refresh_token")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ms_refresh_returns_token_data():
    adapter = _make_adapter()
    resp_data = {"access_token": "new-access", "expires_in": 3600}
    resp = _make_httpx_resp(status_code=200, json_data=resp_data)
    mock_client = _make_httpx_client(resp)

    with (
        patch.dict(
            "os.environ",
            {
                "MICROSOFT_CLIENT_ID": "cid",
                "MICROSOFT_CLIENT_SECRET": "csec",
                "MICROSOFT_TENANT_ID": "common",
            },
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter._ms_refresh("some-refresh-token")

    assert result["access_token"] == "new-access"


# ---------------------------------------------------------------------------
# _get_fresh_access_token
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_fresh_access_token_raises_when_no_tokens():
    adapter = _make_adapter()
    with patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="No Outlook tokens"):
            await adapter._get_fresh_access_token(_UID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_fresh_access_token_returns_decrypted():
    adapter = _make_adapter()
    tokens = {
        "access_token": "enc-access",
        "refresh_token": "",
        "expiry": _NOW + timedelta(hours=2),  # Not near expiry
    }
    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            return_value="decrypted-access",
        ),
    ):
        result = await adapter._get_fresh_access_token(_UID)

    assert result == "decrypted-access"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_fresh_access_token_refreshes_when_near_expiry():
    adapter = _make_adapter()
    tokens = {
        "access_token": "enc-access",
        "refresh_token": "enc-refresh",
        "expiry": _NOW + timedelta(seconds=100),  # expires in <5min
        "source": "provider",
        "source_id": str(uuid.uuid4()),
    }

    with (
        patch.object(adapter, "_get_user_tokens", AsyncMock(return_value=tokens)),
        patch(
            "src.infrastructure.security.token_encryption.decrypt_token",
            side_effect=lambda x: x,  # return as-is
        ),
        patch.object(
            adapter,
            "_ms_refresh",
            AsyncMock(
                return_value={"access_token": "refreshed-access", "expires_in": 3600}
            ),
        ),
        patch.object(adapter, "_persist_refreshed_tokens", AsyncMock()),
    ):
        result = await adapter._get_fresh_access_token(_UID)

    assert result == "refreshed-access"


# ---------------------------------------------------------------------------
# list_recent_emails
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_headers", AsyncMock(side_effect=Exception("no tokens"))
    ):
        result = await adapter.list_recent_emails(_UID, since=_NOW)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_recent_emails_returns_parsed_messages():
    adapter = _make_adapter()
    msg_data = {
        "value": [
            {
                "id": "msg1",
                "subject": "Meeting invite",
                "from": {"emailAddress": {"address": "a@b.com", "name": "Alice"}},
                "toRecipients": [],
                "ccRecipients": [],
                "receivedDateTime": "2024-06-01T10:00:00Z",
                "body": {"contentType": "text", "content": "Let's meet"},
                "bodyPreview": "Let's meet",
                "hasAttachments": False,
                "isRead": True,
                "conversationId": "thread1",
            }
        ]
    }
    resp = _make_httpx_resp(200, msg_data)
    mock_client = _make_httpx_client(resp)

    with (
        patch.object(
            adapter,
            "_get_headers",
            AsyncMock(return_value={"Authorization": "Bearer x"}),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.list_recent_emails(_UID, since=_NOW)

    assert len(result) == 1
    assert result[0].subject == "Meeting invite"


# ---------------------------------------------------------------------------
# get_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_email_returns_none_on_failure():
    adapter = _make_adapter()
    with patch.object(adapter, "_get_headers", AsyncMock(side_effect=Exception("err"))):
        result = await adapter.get_email(_UID, "msg1")
    assert result is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_email_returns_message():
    adapter = _make_adapter()
    msg_data = {
        "id": "msg1",
        "subject": "Test",
        "from": {"emailAddress": {"address": "s@e.com", "name": "Sender"}},
        "toRecipients": [],
        "ccRecipients": [],
        "receivedDateTime": "2024-06-01T10:00:00Z",
        "body": {"contentType": "text", "content": "body"},
        "bodyPreview": "body preview",
        "hasAttachments": False,
        "isRead": True,
        "conversationId": "th1",
    }
    resp = _make_httpx_resp(200, msg_data)
    mock_client = _make_httpx_client(resp)
    mock_client.get = AsyncMock(return_value=resp)

    with (
        patch.object(adapter, "_get_headers", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.get_email(_UID, "msg1")

    assert result is not None
    assert result.subject == "Test"


# ---------------------------------------------------------------------------
# get_thread_messages
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_thread_messages_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_headers", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.get_thread_messages(_UID, "conv1")
    assert result == []


# ---------------------------------------------------------------------------
# create_draft_reply
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_returns_draft_id():
    adapter = _make_adapter()
    resp = _make_httpx_resp(201, {"id": "draft-ms-1"})
    mock_client = _make_httpx_client(resp)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch.object(adapter, "_get_headers", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.create_draft_reply(
            _UID, "th1", "dest@x.com", "Re: Test", "body text"
        )

    assert result == "draft-ms-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_headers", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.create_draft_reply(
            _UID, "th1", "d@e.com", "Subj", "body"
        )
    assert result == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_draft_reply_with_cc():
    adapter = _make_adapter()
    resp = _make_httpx_resp(201, {"id": "draft-cc"})
    mock_client = _make_httpx_client(resp)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch.object(adapter, "_get_headers", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.create_draft_reply(
            _UID, "th1", "dest@x.com", "Re: Test", "body", cc="cc1@x.com, cc2@x.com"
        )

    assert result == "draft-cc"


# ---------------------------------------------------------------------------
# send_draft
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_draft_returns_draft_id_on_success():
    adapter = _make_adapter()
    resp = _make_httpx_resp(202)
    mock_client = _make_httpx_client(resp)
    mock_client.post = AsyncMock(return_value=resp)

    with (
        patch.object(adapter, "_get_headers", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.send_draft(_UID, "draft-ms-1")

    assert result == "draft-ms-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_draft_returns_empty_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_headers", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.send_draft(_UID, "draft-ms-1")
    assert result == ""


# ---------------------------------------------------------------------------
# mark_processed
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_processed_returns_true_on_200():
    adapter = _make_adapter()
    resp = _make_httpx_resp(200)
    mock_client = _make_httpx_client(resp)
    mock_client.patch = AsyncMock(return_value=resp)

    with (
        patch.object(adapter, "_get_headers", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await adapter.mark_processed(_UID, "msg1")

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_processed_returns_false_on_failure():
    adapter = _make_adapter()
    with patch.object(
        adapter, "_get_headers", AsyncMock(side_effect=Exception("fail"))
    ):
        result = await adapter.mark_processed(_UID, "msg1")
    assert result is False


# ---------------------------------------------------------------------------
# _parse_graph_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_graph_message_basic():
    from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

    msg = {
        "id": "msg1",
        "subject": "Team Meeting",
        "from": {"emailAddress": {"address": "boss@co.com", "name": "Boss"}},
        "toRecipients": [{"emailAddress": {"address": "me@co.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "team@co.com"}}],
        "receivedDateTime": "2024-06-01T10:00:00Z",
        "body": {"contentType": "text", "content": "Meeting at 2pm"},
        "bodyPreview": "Meeting at 2pm",
        "hasAttachments": False,
        "isRead": True,
        "conversationId": "conv-1",
    }
    email = OutlookEmailAdapter._parse_graph_message(msg, _UID)
    assert email.subject == "Team Meeting"
    assert email.sender_email == "boss@co.com"
    assert "me@co.com" in email.recipients
    assert "team@co.com" in email.cc


@pytest.mark.unit
def test_parse_graph_message_html_body_stripped():
    from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

    msg = {
        "id": "msg2",
        "subject": "HTML msg",
        "from": {"emailAddress": {"address": "a@b.com"}},
        "toRecipients": [],
        "ccRecipients": [],
        "receivedDateTime": "2024-06-01T10:00:00Z",
        "body": {"contentType": "html", "content": "<p>Hello <b>World</b></p>"},
        "bodyPreview": "",
        "hasAttachments": False,
        "isRead": True,
        "conversationId": "conv-2",
    }
    email = OutlookEmailAdapter._parse_graph_message(msg, _UID)
    assert "<b>" not in email.body_text
    assert "World" in email.body_text


@pytest.mark.unit
def test_parse_graph_message_bad_date():
    from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

    msg = {
        "id": "msg3",
        "subject": "Bad Date",
        "from": {"emailAddress": {"address": "x@y.com"}},
        "toRecipients": [],
        "ccRecipients": [],
        "receivedDateTime": "not-a-date",
        "body": {"contentType": "text", "content": "body"},
        "bodyPreview": "",
        "hasAttachments": False,
        "isRead": False,
        "conversationId": "c3",
    }
    email = OutlookEmailAdapter._parse_graph_message(msg, _UID)
    assert email.received_at is not None  # Should use now() fallback


# ---------------------------------------------------------------------------
# _persist_refreshed_tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_refreshed_tokens_updates_provider_connection():
    adapter = _make_adapter()

    conn = MagicMock()
    conn.id = uuid.uuid4()
    session = _make_session_first(conn)
    adapter._db_session_factory = MagicMock(return_value=session)

    with patch(
        "src.infrastructure.security.token_encryption.encrypt_token",
        return_value="encrypted",
    ):
        await adapter._persist_refreshed_tokens(
            user_id=_UID,
            source="provider",
            source_id=str(conn.id),
            access_token="new-access",
            refresh_token="new-refresh",
            expires_in=3600,
        )

    session.commit.assert_called_once()
