"""
WhatsApp Intelligence Service — processes inbound WhatsApp messages,
detects meeting commitments using MessageHookService, and creates
Google Calendar events automatically.

Works identically to email intelligence but for WhatsApp text messages.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.infrastructure.whatsapp.webhook_adapter import WhatsAppMessage

logger = logging.getLogger("calendar_agent.whatsapp_intelligence")


@dataclass
class WhatsAppProcessResult:
    message_id: str
    from_phone: str
    text: str
    has_meeting: bool
    event_created: bool
    event_title: str | None
    event_start: str | None
    event_end: str | None
    google_event_id: str | None
    reply_sent: bool
    error: str | None


class WhatsAppIntelligenceService:
    """
    Scans inbound WhatsApp messages for meeting commitments and creates
    calendar events that sync to Google Calendar.

    Reuses MessageHookService for LLM-based commitment detection.
    """

    def __init__(
        self,
        message_hook_service: Any,  # MessageHookService
        calendar_adapter: Any,  # ProviderAwareCalendarAdapter
        db_session_factory: Any,
        whatsapp_adapter: Any,  # WhatsAppWebhookAdapter
        access_token: str = "",
        phone_number_id: str = "",
        auto_reply: bool = True,
    ) -> None:
        self._hook = message_hook_service
        self._calendar = calendar_adapter
        self._db = db_session_factory
        self._wa = whatsapp_adapter
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._auto_reply = auto_reply

    async def _resolve_user_id(self, from_phone: str) -> uuid.UUID | None:
        """
        Resolve the calendar owner for an incoming WhatsApp message.

        Strategy (first match wins):
        1. User with Google tokens — owns the calendar being managed.
        2. Any user in the database — for single-agent installs without OAuth yet.

        The phone_number column is intentionally not queried because the schema
        does not define it.  All messages are routed to the agent owner's calendar
        regardless of the sender, which is the correct behaviour when a single
        WhatsApp number serves as the agent's intake channel.
        """
        if not self._db:
            return None
        try:
            from sqlalchemy import text

            async with self._db() as session:
                # Primary: user with an active Google OAuth token
                r = await session.execute(
                    text(
                        "SELECT id FROM users "
                        "WHERE google_access_token IS NOT NULL "
                        "ORDER BY created_at ASC LIMIT 1"
                    )
                )
                row = r.fetchone()
                if row:
                    uid = row[0]
                    return uuid.UUID(uid) if isinstance(uid, str) else uid

                # Secondary: first registered user (may connect Google later)
                r2 = await session.execute(
                    text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
                )
                row2 = r2.fetchone()
                if row2:
                    uid2 = row2[0]
                    logger.warning(
                        "No Google-authenticated user found; routing WhatsApp "
                        "message to first user in DB (%s)", uid2
                    )
                    return uuid.UUID(uid2) if isinstance(uid2, str) else uid2

        except Exception as exc:
            logger.warning("User lookup failed: %s", exc)
        return None

    async def process_message(
        self,
        msg: WhatsAppMessage,
        user_timezone: str = "UTC",
    ) -> WhatsAppProcessResult:
        """
        Main entry point — process a single WhatsApp message:
        1. Run MessageHookService to detect meeting commitment
        2. If confident, create the calendar event (pushes to Google)
        3. Send a WhatsApp reply confirming the event (if configured)
        """
        result = WhatsAppProcessResult(
            message_id=msg.message_id,
            from_phone=msg.from_phone,
            text=msg.text,
            has_meeting=False,
            event_created=False,
            event_title=None,
            event_start=None,
            event_end=None,
            google_event_id=None,
            reply_sent=False,
            error=None,
        )

        try:
            user_id = await self._resolve_user_id(msg.from_phone)

            if user_id is None:
                logger.error(
                    "No calendar owner found in DB — cannot create event for "
                    "WhatsApp msg %s. Register/login via the web UI first.",
                    msg.message_id,
                )
                result.error = "No authenticated user found. Please sign in via the app."
                return result

            # Use MessageHookService to detect commitment and auto-create event
            hook_result = await self._hook.process_message(
                user_id=user_id,
                message_text=msg.text,
                sender=msg.display_phone,
                source="whatsapp",
                user_timezone=user_timezone,
                auto_create=True,
            )

            if not hook_result.get("detected"):
                logger.debug("No meeting commitment in WhatsApp msg %s", msg.message_id)
                return result

            result.has_meeting = True
            result.event_title = hook_result.get("event_summary")
            result.event_start = hook_result.get("proposed_start") or hook_result.get("start")
            result.event_end = hook_result.get("proposed_end") or hook_result.get("end")

            # Retrieve event created by MessageHookService (if auto-created)
            created_event = hook_result.get("created_event") or hook_result.get("event")
            if created_event or hook_result.get("action") == "created":
                result.event_created = True
                # Use precise start/end from the created event if available
                if hook_result.get("start"):
                    result.event_start = hook_result["start"]
                if hook_result.get("end"):
                    result.event_end = hook_result["end"]
                result.google_event_id = (
                    getattr(created_event, "provider_event_id", None)
                    if created_event
                    else hook_result.get("event_id")
                )
                logger.info(
                    "WhatsApp msg %s → created event '%s' (google_id=%s)",
                    msg.message_id,
                    result.event_title,
                    result.google_event_id,
                )

            # Send confirmation reply
            if self._auto_reply and self._access_token and self._phone_number_id:
                if result.event_created:
                    reply_text = (
                        f"✅ Got it! I've added '{result.event_title}' to your calendar"
                        + (
                            f" on {result.event_start[:10]}."
                            if result.event_start
                            else "."
                        )
                    )
                else:
                    reply_text = (
                        f"📅 I noticed a potential meeting: '{result.event_title}'. "
                        "Reply 'confirm' to add it to your calendar."
                    )

                result.reply_sent = await self._wa.send_reply(
                    to_phone=msg.from_phone,
                    message_text=reply_text,
                    access_token=self._access_token,
                    phone_number_id=self._phone_number_id,
                )

        except Exception as exc:
            logger.exception("WhatsApp intelligence error for msg %s", msg.message_id)
            result.error = str(exc)

        return result
