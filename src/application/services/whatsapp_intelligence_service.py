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
        message_hook_service: Any,          # MessageHookService
        calendar_adapter: Any,              # ProviderAwareCalendarAdapter
        db_session_factory: Any,
        whatsapp_adapter: Any,              # WhatsAppWebhookAdapter
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
        Find the app user whose linked phone matches from_phone.
        Falls back to the first user with a Google token (for demo/single-user).
        """
        if not self._db:
            return None
        try:
            from sqlalchemy import text

            async with self._db() as session:
                # Try to match by phone number stored in users table
                r = await session.execute(
                    text(
                        "SELECT id FROM users WHERE phone_number=:ph OR phone_number=:ph2 LIMIT 1"
                    ),
                    {"ph": from_phone, "ph2": "+" + from_phone},
                )
                row = r.fetchone()
                if row:
                    return uuid.UUID(row[0]) if isinstance(row[0], str) else row[0]

                # Demo fallback: use the first user with Google tokens
                r2 = await session.execute(
                    text(
                        "SELECT id FROM users WHERE google_access_token IS NOT NULL LIMIT 1"
                    )
                )
                row2 = r2.fetchone()
                if row2:
                    return uuid.UUID(row2[0]) if isinstance(row2[0], str) else row2[0]
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
            google_event_id=None,
            reply_sent=False,
            error=None,
        )

        try:
            user_id = await self._resolve_user_id(msg.from_phone)

            # Use MessageHookService to detect commitment
            hook_result = await self._hook.process_message(
                text=msg.text,
                user_id=user_id,
                source="whatsapp",
                sender_phone=msg.from_phone,
                user_timezone=user_timezone,
                now_utc=datetime.fromtimestamp(msg.timestamp, tz=timezone.utc)
                if msg.timestamp
                else datetime.now(timezone.utc),
            )

            if not hook_result.get("has_commitment"):
                logger.debug(
                    "No meeting commitment in WhatsApp msg %s", msg.message_id
                )
                return result

            result.has_meeting = True
            result.event_title = hook_result.get("event_summary")
            result.event_start = hook_result.get("proposed_start")

            # Retrieve event created by MessageHookService (if auto-created)
            created_event = hook_result.get("created_event")
            if created_event:
                result.event_created = True
                result.google_event_id = getattr(
                    created_event, "provider_event_id", None
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
