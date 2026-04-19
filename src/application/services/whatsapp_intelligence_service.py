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

    @staticmethod
    def _detect_timezone(from_phone: str) -> str:
        """
        Detect best-guess timezone from the sender's phone number country prefix.
        Falls back to UTC if unknown.
        """
        _COUNTRY_TZ = {
            "91": "Asia/Kolkata",  # India (+91)
            "1": "America/New_York",  # US/Canada (+1)
            "44": "Europe/London",  # UK (+44)
            "61": "Australia/Sydney",  # Australia (+61)
            "65": "Asia/Singapore",  # Singapore (+65)
            "60": "Asia/Kuala_Lumpur",  # Malaysia (+60)
            "971": "Asia/Dubai",  # UAE (+971)
        }
        phone = from_phone.lstrip("+")
        for prefix, tz in sorted(_COUNTRY_TZ.items(), key=lambda x: -len(x[0])):
            if phone.startswith(prefix):
                return tz
        return "UTC"

    async def _is_duplicate_message(self, message_id: str) -> bool:
        """
        Check if this WhatsApp message_id was already processed.
        Uses the scanned_emails table with provider='whatsapp' for dedup.
        """
        if not self._db or not message_id:
            return False
        try:
            from sqlalchemy import text

            async with self._db() as session:
                r = await session.execute(
                    text(
                        "SELECT 1 FROM scanned_emails "
                        "WHERE provider_message_id = :mid AND provider = 'whatsapp' "
                        "LIMIT 1"
                    ),
                    {"mid": message_id},
                )
                return r.fetchone() is not None
        except Exception as exc:
            logger.warning("Dedup check failed: %s", exc)
            return False

    async def _mark_message_processed(
        self,
        message_id: str,
        from_phone: str,
        msg_text: str,
        user_id: uuid.UUID,
    ) -> None:
        """Record the WhatsApp message_id to prevent double-processing."""
        if not self._db or not message_id:
            return
        try:
            from sqlalchemy import text

            record_id = uuid.uuid4().hex
            snippet = msg_text[:200] if msg_text else ""
            async with self._db() as session:
                await session.execute(
                    text(
                        "INSERT INTO scanned_emails "
                        "(id, user_id, provider_message_id, provider, thread_id, "
                        " subject, sender_email, sender_name, recipients_json, "
                        " body_snippet, body_text, has_attachments, is_read, "
                        " is_actionable, analysis_category, analysis_confidence, "
                        " analysis_summary, scanned_at) "
                        "VALUES "
                        "(:id, :uid, :mid, 'whatsapp', :mid, "
                        " :subject, :sender, :sender, '[]', "
                        " :snippet, :snippet, 0, 1, "
                        " 1, 'whatsapp_message', 1.0, "
                        " 'Processed by WhatsApp intelligence', :now)"
                    ),
                    {
                        "id": record_id,
                        "uid": str(user_id).replace("-", ""),
                        "mid": message_id,
                        "subject": f"WhatsApp from {from_phone}",
                        "sender": from_phone,
                        "snippet": snippet,
                        "now": datetime.now(timezone.utc).isoformat(),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.warning("Failed to mark message as processed: %s", exc)

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
                        "message to first user in DB (%s)",
                        uid2,
                    )
                    return uuid.UUID(uid2) if isinstance(uid2, str) else uid2

        except Exception as exc:
            logger.warning("User lookup failed: %s", exc)
        return None

    async def process_message(
        self,
        msg: WhatsAppMessage,
        user_timezone: str = "",
    ) -> WhatsAppProcessResult:
        """
        Main entry point — process a single WhatsApp message:
        1. Deduplicate by message_id (skip already-processed messages)
        2. Run MessageHookService to detect meeting commitment
        3. If confident, create the calendar event (pushes to Google)
        4. Send a WhatsApp reply confirming the event (if configured)
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

        # Deduplication: skip messages already processed
        if msg.message_id and await self._is_duplicate_message(msg.message_id):
            logger.info(
                "Skipping duplicate WhatsApp message_id=%s from %s",
                msg.message_id,
                msg.from_phone,
            )
            result.error = "duplicate"
            return result

        # Auto-detect timezone from phone number country prefix
        effective_tz = user_timezone or self._detect_timezone(msg.from_phone)

        try:
            user_id = await self._resolve_user_id(msg.from_phone)

            if user_id is None:
                logger.error(
                    "No calendar owner found in DB — cannot create event for "
                    "WhatsApp msg %s. Register/login via the web UI first.",
                    msg.message_id,
                )
                result.error = (
                    "No authenticated user found. Please sign in via the app."
                )
                return result

            # Use MessageHookService to detect commitment and auto-create event
            hook_result = await self._hook.process_message(
                user_id=user_id,
                message_text=msg.text,
                sender=(
                    f"+{msg.from_phone}"
                    if not msg.from_phone.startswith("+")
                    else msg.from_phone
                ),
                source="whatsapp",
                user_timezone=effective_tz,
                auto_create=True,
            )

            if not hook_result.get("detected"):
                logger.debug("No meeting commitment in WhatsApp msg %s", msg.message_id)
                # Still mark as processed so we don't re-scan it
                await self._mark_message_processed(
                    msg.message_id, msg.from_phone, msg.text, user_id
                )
                return result

            result.has_meeting = True
            result.event_title = hook_result.get("event_summary")
            result.event_start = hook_result.get("proposed_start") or hook_result.get(
                "start"
            )
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
                    else hook_result.get("google_event_id")
                )
                logger.info(
                    "WhatsApp msg %s → created event '%s' (google_id=%s)",
                    msg.message_id,
                    result.event_title,
                    result.google_event_id,
                )

            # Mark message as processed (dedup guard for future retries)
            await self._mark_message_processed(
                msg.message_id, msg.from_phone, msg.text, user_id
            )

            # Store sender phone in DB description for traceability
            # (injected into the event description by updating the calendar event)
            try:
                if result.event_created and self._db:
                    from sqlalchemy import text as _text

                    sender_tag = (
                        f"\n\n📱 From WhatsApp: +{msg.from_phone}"
                        if not msg.from_phone.startswith("+")
                        else f"\n\n📱 From WhatsApp: {msg.from_phone}"
                    )
                    # Match on internal id (CHAR(32) without dashes) from hook_result
                    db_event_id = (
                        hook_result.get("event_id")
                        or str(getattr(created_event, "id", "")).replace("-", "")
                    )
                    if db_event_id:
                        async with self._db() as session:
                            await session.execute(
                                _text(
                                    "UPDATE calendar_events "
                                    "SET description = COALESCE(description,'') || :tag "
                                    "WHERE id = :eid AND source = 'whatsapp' "
                                    "AND description NOT LIKE :pattern"
                                ),
                                {
                                    "tag": sender_tag,
                                    "eid": db_event_id,
                                    "pattern": "%From WhatsApp%",
                                },
                            )
                            await session.commit()
            except Exception:
                pass  # non-fatal

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
