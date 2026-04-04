"""
WebSocket endpoint for real-time streaming agent responses.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


@ws_router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for streaming chat.
    Protocol:
      - Client sends: {"message": "...", "conversation_id": "...", "token": "..."}
      - Server streams: {"type": "token|complete|error", "content": "..."}
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            message = payload.get("message", "")
            jwt_token = payload.get("token")
            conv_id = payload.get("conversation_id")

            # --- Authenticate ---
            container = websocket.app.state.container  # type: ignore[union-attr]
            user = None
            if jwt_token:
                try:
                    jwt_svc = container.jwt_service()
                    claims = jwt_svc.decode_token(jwt_token)
                    user_id_str = claims.get("sub")
                    if user_id_str:
                        from src.infrastructure.persistence.user_repository import (
                            SQLAlchemyUserRepository,
                        )

                        db = container.database()
                        async with db.session_factory() as session:
                            repo = SQLAlchemyUserRepository(session)
                            user = await repo.get_by_id(UUID(user_id_str))
                except Exception:
                    pass

            if not user:
                await websocket.send_json(
                    {"type": "error", "content": "Authentication required"}
                )
                continue

            # --- Process via ChatService ---
            await websocket.send_json({"type": "token", "content": "Thinking..."})

            try:
                from src.application.dto import ChatRequestDTO
                from src.infrastructure.persistence.conversation_repository import (
                    SQLAlchemyConversationRepository,
                )

                db = container.database()
                async with db.session_factory() as session:
                    from src.api.rest.routes import _build_chat_service

                    chat_svc = _build_chat_service(
                        container, SQLAlchemyConversationRepository(session)
                    )
                    req = ChatRequestDTO(
                        message=message,
                        conversation_id=UUID(conv_id) if conv_id else None,
                    )
                    response = await chat_svc.handle_message(
                        user_id=user.id,
                        request=req,
                        plan_limit=user.get_request_limit(),
                    )
                    await session.commit()

                await websocket.send_json(
                    {
                        "type": "complete",
                        "content": response.message,
                        "conversation_id": str(response.conversation_id),
                    }
                )
            except Exception as exc:
                await websocket.send_json({"type": "error", "content": str(exc)})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
