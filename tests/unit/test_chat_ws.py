"""Tests for src/api/websocket/chat_ws.py."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container(user=None, claims=None):
    """Build a mock DI container."""
    container = MagicMock()

    jwt_svc = MagicMock()
    jwt_svc.decode_token = MagicMock(return_value=claims or {"sub": str(uuid.uuid4())})
    container.jwt_service = MagicMock(return_value=jwt_svc)

    db = MagicMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    db.session_factory = MagicMock(return_value=session)
    container.database = MagicMock(return_value=db)

    return container, db, session, jwt_svc


def _make_app(container):
    app = MagicMock()
    app.state.container = container
    return app


# ---------------------------------------------------------------------------
# Test the WebSocket handler directly
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_auth_failure_closes_with_4001():
    """Returns error and closes when authentication fails (no token)."""
    from src.api.websocket.chat_ws import websocket_chat

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.app = _make_app(MagicMock())
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()

    # No token in payload
    ws.receive_text = AsyncMock(return_value=json.dumps({"message": "hi"}))

    container = MagicMock()
    jwt_svc = MagicMock()
    jwt_svc.decode_token = MagicMock(side_effect=Exception("no token"))
    container.jwt_service = MagicMock(return_value=jwt_svc)
    ws.app.state.container = container

    await websocket_chat(ws)

    ws.send_json.assert_any_call(
        {"type": "error", "content": "Authentication required"}
    )
    ws.close.assert_called_once_with(code=4001)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_sends_thinking_message():
    """Sends 'Thinking...' token after auth succeeds."""
    from src.api.websocket.chat_ws import websocket_chat

    user_id = uuid.uuid4()
    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.get_request_limit = MagicMock(return_value=100)

    container, db, session, jwt_svc = _make_container(
        user=mock_user, claims={"sub": str(user_id)}
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=mock_user)

    mock_response = MagicMock()
    mock_response.message = "You have 2 events."
    mock_response.conversation_id = uuid.uuid4()

    mock_chat_svc = AsyncMock()
    mock_chat_svc.handle_message = AsyncMock(return_value=mock_response)

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app = _make_app(container)

    # One valid message then disconnect
    from fastapi import WebSocketDisconnect

    ws.receive_text = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "message": "list my events",
                    "token": "Bearer fake.fake.fake",
                    "conversation_id": str(uuid.uuid4()),
                }
            ),
            WebSocketDisconnect(),
        ]
    )

    with (
        patch(
            "src.infrastructure.persistence.user_repository.SQLAlchemyUserRepository",
            return_value=mock_repo,
        ),
        patch(
            "src.api.rest.routes._build_chat_service",
            return_value=mock_chat_svc,
        ),
        patch(
            "src.infrastructure.persistence.conversation_repository.SQLAlchemyConversationRepository",
        ),
        patch(
            "src.application.dto.ChatRequestDTO",
            return_value=MagicMock(),
        ),
    ):
        await websocket_chat(ws)

    # Should have sent "Thinking..." at some point
    calls = [str(call) for call in ws.send_json.call_args_list]
    assert any("Thinking" in c for c in calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_sends_complete_with_response():
    """Sends 'complete' message with response content."""
    from src.api.websocket.chat_ws import websocket_chat

    user_id = uuid.uuid4()
    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.get_request_limit = MagicMock(return_value=100)

    container, db, session, jwt_svc = _make_container(claims={"sub": str(user_id)})

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=mock_user)

    conv_id = uuid.uuid4()
    mock_response = MagicMock()
    mock_response.message = "Here are your events."
    mock_response.conversation_id = conv_id

    mock_chat_svc = AsyncMock()
    mock_chat_svc.handle_message = AsyncMock(return_value=mock_response)

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app = _make_app(container)

    from fastapi import WebSocketDisconnect

    ws.receive_text = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "message": "list events",
                    "token": "hdr.payload.sig",
                    "conversation_id": None,
                }
            ),
            WebSocketDisconnect(),
        ]
    )

    with (
        patch(
            "src.infrastructure.persistence.user_repository.SQLAlchemyUserRepository",
            return_value=mock_repo,
        ),
        patch("src.api.rest.routes._build_chat_service", return_value=mock_chat_svc),
        patch(
            "src.infrastructure.persistence.conversation_repository.SQLAlchemyConversationRepository"
        ),
        patch("src.application.dto.ChatRequestDTO", return_value=MagicMock()),
    ):
        await websocket_chat(ws)

    complete_calls = [
        c[0][0]
        for c in ws.send_json.call_args_list
        if isinstance(c[0][0], dict) and c[0][0].get("type") == "complete"
    ]
    assert len(complete_calls) == 1
    assert complete_calls[0]["content"] == "Here are your events."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_handler_error_sends_error_message():
    """When chat service raises, sends error message to client."""
    from src.api.websocket.chat_ws import websocket_chat

    user_id = uuid.uuid4()
    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.get_request_limit = MagicMock(return_value=100)

    container, db, session, jwt_svc = _make_container(claims={"sub": str(user_id)})

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=mock_user)

    mock_chat_svc = AsyncMock()
    mock_chat_svc.handle_message = AsyncMock(side_effect=Exception("service error"))

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app = _make_app(container)

    from fastapi import WebSocketDisconnect

    ws.receive_text = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "message": "create event",
                    "token": "hdr.p.s",
                    "conversation_id": None,
                }
            ),
            WebSocketDisconnect(),
        ]
    )

    with (
        patch(
            "src.infrastructure.persistence.user_repository.SQLAlchemyUserRepository",
            return_value=mock_repo,
        ),
        patch("src.api.rest.routes._build_chat_service", return_value=mock_chat_svc),
        patch(
            "src.infrastructure.persistence.conversation_repository.SQLAlchemyConversationRepository"
        ),
        patch("src.application.dto.ChatRequestDTO", return_value=MagicMock()),
    ):
        await websocket_chat(ws)

    error_calls = [
        c[0][0]
        for c in ws.send_json.call_args_list
        if isinstance(c[0][0], dict) and c[0][0].get("type") == "error"
    ]
    assert any("service error" in c.get("content", "") for c in error_calls)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_handles_outer_exception_gracefully():
    """Catches unexpected outer exception and tries to send error."""
    from src.api.websocket.chat_ws import websocket_chat

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()

    # app.state.container raises something unexpected
    ws.app = MagicMock()
    ws.app.state.container = None

    ws.receive_text = AsyncMock(
        return_value=json.dumps(
            {
                "message": "hello",
                "token": "a.b.c",
            }
        )
    )

    # Should not raise; outer exception is caught
    await websocket_chat(ws)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_chat_user_not_found_closes():
    """If user lookup returns None, send error and close."""
    from src.api.websocket.chat_ws import websocket_chat

    user_id = uuid.uuid4()
    container, db, session, jwt_svc = _make_container(claims={"sub": str(user_id)})

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=None)  # no user found

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.app = _make_app(container)

    ws.receive_text = AsyncMock(
        return_value=json.dumps(
            {
                "message": "hello",
                "token": "hdr.payload.sig",
            }
        )
    )

    with patch(
        "src.infrastructure.persistence.user_repository.SQLAlchemyUserRepository",
        return_value=mock_repo,
    ):
        await websocket_chat(ws)

    ws.close.assert_called_once_with(code=4001)
