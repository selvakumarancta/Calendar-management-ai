"""
WhatsApp Webhook Routes — handles Meta Cloud API webhook events.

Two endpoints:
  GET  /api/v1/webhooks/whatsapp          — webhook verification challenge
  POST /api/v1/webhooks/whatsapp          — inbound message events
  GET  /api/v1/webhooks/whatsapp/events   — history of WhatsApp-created events (auth required)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from src.api.dependencies import get_container, get_current_user
from src.config.container import Container

logger = logging.getLogger("calendar_agent.whatsapp_routes")

whatsapp_router = APIRouter()


# ---------------------------------------------------------------------------
# GET — Meta webhook verification challenge
# ---------------------------------------------------------------------------


@whatsapp_router.get(
    "/whatsapp",
    response_class=PlainTextResponse,
    summary="WhatsApp webhook verification",
    include_in_schema=False,
)
async def whatsapp_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    container: Container = Depends(get_container),
) -> PlainTextResponse:
    """
    Meta calls this GET endpoint when you register the webhook URL.

    Accepts the verify_token from any enabled org config OR the global
    .env verify_token — whichever matches first.
    """
    from sqlalchemy import text as _sql

    # Check org-level verify tokens first
    if hub_mode == "subscribe" and hub_verify_token:
        db = container.database()
        async with db.session_factory() as _session:
            r = await _session.execute(
                _sql(
                    "SELECT 1 FROM org_whatsapp_configs "
                    "WHERE verify_token = :vt AND enabled = 1 LIMIT 1"
                ),
                {"vt": hub_verify_token},
            )
            if r.fetchone():
                return PlainTextResponse(content=hub_challenge)

    # Fallback: global .env verify token
    adapter = container.whatsapp_webhook_adapter()
    challenge = adapter.verify_challenge(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verification token",
        )
    return PlainTextResponse(content=challenge)


# ---------------------------------------------------------------------------
# POST — inbound WhatsApp messages
# ---------------------------------------------------------------------------


@whatsapp_router.post(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="WhatsApp inbound message webhook",
    tags=["WhatsApp"],
)
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str = Header("", alias="X-Hub-Signature-256"),
    container: Container = Depends(get_container),
) -> dict:
    """
    Receives inbound WhatsApp messages from Meta Cloud API.

    Multi-tenant dispatch: the phone_number_id in the Meta payload is used
    to look up the correct org's WhatsApp config from org_whatsapp_configs.
    Falls back to the global .env config if no org row is found (single-tenant
    / dev mode).

    For each text message:
    1. Detects meeting commitments using AI
    2. Creates a calendar event automatically
    3. Syncs the event to Google Calendar
    4. Sends a WhatsApp reply to the sender confirming the event
    """
    from sqlalchemy import text as _sql

    raw_body = await request.body()
    payload = await request.json()

    adapter = container.whatsapp_webhook_adapter()

    # Optional HMAC verification (global secret; org-level checked below)
    if not adapter.verify_signature(raw_body, x_hub_signature_256):
        logger.warning("WhatsApp webhook signature mismatch — rejecting")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # Parse messages from payload
    messages = adapter.parse_messages(payload)

    if not messages:
        return {"status": "ok", "processed": 0}

    # --- Multi-tenant: resolve org config by phone_number_id ---
    incoming_phone_id = (
        payload.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("metadata", {})
        .get("phone_number_id", "")
    )

    org_cfg = None
    if incoming_phone_id:
        db = container.database()
        async with db.session_factory() as _session:
            r = await _session.execute(
                _sql(
                    "SELECT org_id, access_token, phone_number_id, verify_token, "
                    "auto_reply, enabled "
                    "FROM org_whatsapp_configs "
                    "WHERE phone_number_id = :pid AND enabled = 1 LIMIT 1"
                ),
                {"pid": incoming_phone_id},
            )
            row = r.fetchone()
            if row:
                org_cfg = {
                    "org_id": row[0],
                    "access_token": row[1],
                    "phone_number_id": row[2],
                    "verify_token": row[3],
                    "auto_reply": bool(row[4]),
                }

    if org_cfg:
        # Build a dedicated service instance with the org's credentials
        from src.application.services.whatsapp_intelligence_service import (
            WhatsAppIntelligenceService,
        )

        db = container.database()
        svc = WhatsAppIntelligenceService(
            message_hook_service=container.message_hook_service(),
            calendar_adapter=container.calendar_adapter(),
            db_session_factory=db.session_factory,
            whatsapp_adapter=adapter,
            access_token=org_cfg["access_token"],
            phone_number_id=org_cfg["phone_number_id"],
            auto_reply=org_cfg["auto_reply"],
        )
        logger.info(
            "WhatsApp webhook: dispatching to org %s (phone_id=%s)",
            org_cfg["org_id"],
            incoming_phone_id,
        )
    else:
        # Fallback: single-tenant / dev mode using global .env config
        svc = container.whatsapp_intelligence_service()
        logger.debug(
            "WhatsApp webhook: no org config for phone_id=%s, using global config",
            incoming_phone_id,
        )

    results = []
    for msg in messages:
        logger.info(
            "Processing WhatsApp message from %s: %s", msg.from_phone, msg.text[:80]
        )
        result = await svc.process_message(msg)
        results.append(
            {
                "message_id": result.message_id,
                "from": result.from_phone,
                "has_meeting": result.has_meeting,
                "event_created": result.event_created,
                "event_title": result.event_title,
                "event_start": result.event_start,
                "event_end": result.event_end,
                "google_event_id": result.google_event_id,
                "reply_sent": result.reply_sent,
            }
        )
        if result.event_created:
            logger.info(
                "Event created from WhatsApp: '%s' (google_id=%s)",
                result.event_title,
                result.google_event_id,
            )

    return {"status": "ok", "processed": len(results), "results": results}


# ---------------------------------------------------------------------------
# GET — WhatsApp event history (authenticated)
# ---------------------------------------------------------------------------


@whatsapp_router.get(
    "/whatsapp/events",
    summary="WhatsApp-created calendar events",
    tags=["WhatsApp"],
)
async def whatsapp_event_history(
    limit: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """
    Returns recent calendar events created via WhatsApp messages,
    newest first. Requires JWT authentication.
    """
    from sqlalchemy import text

    db = container.database()
    async with db.session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, title, description, location,
                       start_time, end_time, is_all_day,
                       provider_event_id, created_at,
                       COALESCE(source, 'manual') AS source
                FROM calendar_events
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"uid": str(current_user.id).replace("-", ""), "lim": limit},
        )
        events = []
        for row in rows.fetchall():
            events.append(
                {
                    "id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "location": row[3],
                    "start_time": str(row[4]) if row[4] else None,
                    "end_time": str(row[5]) if row[5] else None,
                    "is_all_day": bool(row[6]),
                    "google_event_id": row[7],
                    "created_at": str(row[8]) if row[8] else None,
                    "source": row[9] or "manual",
                }
            )
    return {"events": events, "total": len(events)}


# ---------------------------------------------------------------------------
# POST — Replay previous WhatsApp messages (authenticated)
# ---------------------------------------------------------------------------


@whatsapp_router.post(
    "/whatsapp/replay",
    summary="Replay previous WhatsApp messages to create calendar events",
    tags=["WhatsApp"],
)
async def whatsapp_replay(
    request: Request,
    current_user=Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """
    Accepts a list of previous WhatsApp message texts and processes each
    one for meeting commitments, creating Google Calendar events automatically.

    Request body:
        {
            "messages": [
                {"text": "...", "from": "919876543210"},
                ...
            ],
            "from_phone": "919876543210"   (optional default sender)
        }
    """
    from src.infrastructure.whatsapp.webhook_adapter import WhatsAppMessage

    body = await request.json()
    messages_input = body.get("messages", [])
    default_phone = body.get("from_phone", "replay")

    if not messages_input:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="messages list is required and must not be empty",
        )

    svc = container.whatsapp_intelligence_service()
    results = []

    for i, item in enumerate(messages_input):
        text = (
            item.get("text", "").strip()
            if isinstance(item, dict)
            else str(item).strip()
        )
        from_phone = (
            item.get("from", default_phone) if isinstance(item, dict) else default_phone
        )

        if not text:
            continue

        msg = WhatsAppMessage(
            message_id=f"replay_{i}_{id(text)}",
            from_phone=from_phone,
            display_phone=from_phone,
            text=text,
            timestamp=0,
            phone_number_id="replay",
        )

        result = await svc.process_message(msg)
        results.append(
            {
                "index": i,
                "text": text[:100] + ("…" if len(text) > 100 else ""),
                "has_meeting": result.has_meeting,
                "event_created": result.event_created,
                "event_title": result.event_title,
                "event_start": result.event_start,
                "event_end": result.event_end,
                "google_event_id": result.google_event_id,
                "error": result.error,
            }
        )
        logger.info(
            "Replay msg[%d] '%s…' → has_meeting=%s event_created=%s",
            i,
            text[:50],
            result.has_meeting,
            result.event_created,
        )

    created = sum(1 for r in results if r["event_created"])
    detected = sum(1 for r in results if r["has_meeting"])
    return {
        "processed": len(results),
        "meetings_detected": detected,
        "events_created": created,
        "results": results,
    }
