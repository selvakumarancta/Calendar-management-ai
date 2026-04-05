"""
Message Hook Service — turns arbitrary message text into calendar events.

Accepts raw text from any source (Slack, SMS, WhatsApp, email body, etc.)
and decides whether the message contains a scheduling commitment.

If yes, either:
  (a) Creates the calendar event immediately (if confidence is high), or
  (b) Returns a structured suggestion for the caller to confirm

Works for messages like:
  "Hey let's grab coffee Friday at 2pm"
  "Call me tomorrow morning, I'm free until noon"
  "@here team sync at 10am Monday in conf room A"
  "Can we meet next week? I'm free Mon/Wed afternoons"

No LangChain overhead — single focused LLM call with structured output.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.message_hook")

_HOOK_SYSTEM = """You are an AI that extracts meeting commitments from messages.

Analyze the given message and determine:
1. Does it contain a scheduling COMMITMENT (not just a vague wish)?
2. If yes, extract the meeting details.

A "commitment" is when at least one party is expressing willingness to meet at a specific time or clearly proposing one.

Respond ONLY with JSON:
{{
  "has_commitment": boolean,
  "confidence": 0.0-1.0,
  "event_summary": "string or null",
  "proposed_start": "ISO8601 with offset or null",
  "proposed_end": "ISO8601 with offset or null",
  "duration_estimate_minutes": integer or null,
  "attendees": ["email or name strings"],
  "location": "string or null",
  "notes": "any context worth keeping",
  "is_question": boolean,
  "needs_reply": boolean
}}

Rules:
- proposed_start must be absolute ISO8601 if the date/time is clear; null if only relative hints like "tomorrow"
- If the message is ONLY a question with no committed time, has_commitment=false
- confidence < 0.6 should trigger has_commitment=false
- Today's date and the sender's timezone are provided in the user message."""


class MessageHookService:
    """
    Processes incoming messages and creates calendar events when scheduling
    commitments are detected.
    """

    def __init__(
        self,
        llm_adapter: Any = None,
        calendar_adapter: Any = None,
        db_session_factory: Any = None,
        auto_create_threshold: float = 0.85,
    ) -> None:
        self._llm = llm_adapter
        self._calendar = calendar_adapter
        self._db = db_session_factory
        # Auto-create event without user confirmation above this confidence
        self._auto_threshold = auto_create_threshold

    async def process_message(
        self,
        user_id: uuid.UUID,
        message_text: str,
        sender: str,
        source: str = "unknown",
        user_timezone: str = "UTC",
        auto_create: bool = False,
    ) -> dict:
        """
        Process an incoming message and optionally create a calendar event.

        Args:
            user_id: Calendar owner.
            message_text: Raw message text.
            sender: Who sent the message (name or email).
            source: Where the message came from ("slack", "sms", "email", "text").
            user_timezone: User's timezone for relative time resolution.
            auto_create: Whether to auto-create. Defaults to False.

        Returns:
            dict with detection result and optional created event.
        """
        extraction = await self._extract_commitment(message_text, sender, user_timezone)

        if not extraction.get("has_commitment"):
            return {
                "detected": False,
                "confidence": extraction.get("confidence", 0),
                "reason": "No scheduling commitment detected",
                "source": source,
                "raw_extraction": extraction,
            }

        confidence = extraction.get("confidence", 0)

        should_create = (
            auto_create
            and confidence >= self._auto_threshold
            and extraction.get("proposed_start")
        )

        result: dict = {
            "detected": True,
            "confidence": confidence,
            "event_summary": extraction.get("event_summary"),
            "proposed_start": extraction.get("proposed_start"),
            "proposed_end": extraction.get("proposed_end"),
            "attendees": extraction.get("attendees", []),
            "location": extraction.get("location"),
            "needs_reply": extraction.get("needs_reply", False),
            "source": source,
            "action": "suggested",
        }

        if should_create:
            create_result = await self._create_event_from_extraction(
                user_id, extraction
            )
            result.update(create_result)

        return result

    async def _extract_commitment(
        self,
        message_text: str,
        sender: str,
        user_timezone: str,
    ) -> dict:
        """Use LLM to extract scheduling commitment details."""
        if not self._llm:
            return {"has_commitment": False, "confidence": 0}

        today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
        user_content = (
            f"Message from {sender}:\n{message_text}\n\n"
            f"Today: {today} | Timezone: {user_timezone}"
        )

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _HOOK_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                max_tokens=400,
            )
            text = (
                response if isinstance(response, str) else response.get("content", "")
            ).strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text)
        except Exception as e:
            logger.warning("Message hook extraction failed: %s", e)
            return {"has_commitment": False, "confidence": 0}

    async def _create_event_from_extraction(
        self, user_id: uuid.UUID, extraction: dict
    ) -> dict:
        """Create a calendar event from extracted commitment data."""
        if not self._calendar:
            return {"action": "suggested", "reason": "Calendar adapter not available"}

        try:
            from datetime import timedelta

            start_dt = datetime.fromisoformat(extraction["proposed_start"])

            # Use explicit end if provided, else derive from duration
            if extraction.get("proposed_end"):
                end_dt = datetime.fromisoformat(extraction["proposed_end"])
            else:
                duration = extraction.get("duration_estimate_minutes") or 60
                end_dt = start_dt + timedelta(minutes=duration)

            from src.application.dto import CreateEventDTO

            dto = CreateEventDTO(
                title=extraction.get("event_summary", "Meeting"),
                start_time=start_dt,
                end_time=end_dt,
                description=extraction.get(
                    "notes", "Created by CalendarAgent message hook"
                ),
                location=extraction.get("location", ""),
                attendee_emails=[
                    a for a in extraction.get("attendees", []) if "@" in str(a)
                ],
            )
            event = await self._calendar.create_event(user_id, dto)
            logger.info(
                "Message hook auto-created event: '%s' at %s for user %s",
                dto.title,
                dto.start_time,
                user_id,
            )
            return {
                "action": "created",
                "event_id": getattr(event, "id", ""),
                "title": dto.title,
                "start": dto.start_time.isoformat(),
                "end": dto.end_time.isoformat(),
            }
        except Exception as e:
            logger.error("Failed to create event from message hook: %s", e)
            return {"action": "error", "reason": str(e)}
