"""
Calendar Invite Verification Service — 3-layer safety for calendar invites.

When a draft reply proposes a meeting time, the system remembers a "pending_invite".
ONLY after the user actually sends the draft do we:
  1. Re-read the sent message
  2. Ask the LLM: "Does this sent message STILL confirm the meeting?"
  3. Only if confirmed → send the calendar invite

This prevents sending premature invites when:
- The user edited the draft before sending (changed time, cancelled)
- The thread context changed
- The user decided not to confirm

Three outcomes:
  "send"   — The sent message confirms the invite as-is. Send it.
  "update" — The meeting was confirmed but details changed. Update and send.
  "skip"   — No confirmation. Do NOT send the invite.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.invite_verification")

_VERIFICATION_SYSTEM = """You are verifying whether a user's sent email still confirms a pending calendar invite.

You are given:
1. A pending calendar invite proposal (attendees, time, summary, location)
2. The full email thread history
3. The message the user just sent

Your job: decide whether the sent message confirms, modifies, or cancels the meeting.

Return ONLY a JSON object:
{
  "action": "send" | "update" | "skip",
  "reason": "brief explanation",
  "updated_event_summary": null or string,
  "updated_event_start": null or ISO8601 string,
  "updated_event_end": null or ISO8601 string,
  "updated_attendees": null or [string],
  "updated_location": null or string,
  "add_google_meet": null or boolean
}

Actions:
- "send": Sent message confirms the meeting exactly as proposed.
- "update": Meeting confirmed but details changed (time/attendees/location). Populate updated_* fields.
- "skip": Sent message declines, changes topic, or does not confirm any meeting.

Be conservative — if there is ANY ambiguity, choose "skip".
It is far better to NOT send an invite than to send an unwanted one."""

_VERIFICATION_USER = """Verify whether this sent message confirms the proposed calendar invite.

PENDING INVITE:
  Summary: {summary}
  Start: {start}
  End: {end}
  Attendees: {attendees}
  Location: {location}

THREAD HISTORY (oldest first):
{thread_section}

SENT MESSAGE (from {sender}):
{sent_body}"""


class InviteVerificationService:
    """
    Verifies whether a sent email confirms a pending calendar invite,
    then creates or skips the invite accordingly.
    """

    def __init__(
        self,
        llm_adapter: Any = None,
        calendar_adapter: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._llm = llm_adapter
        self._calendar = calendar_adapter
        self._db = db_session_factory

    async def verify_and_process_invite(
        self,
        user_id: uuid.UUID,
        draft_reply_id: uuid.UUID,
        sent_message_body: str,
        sender_email: str,
        thread_messages: list[dict],
    ) -> dict:
        """
        Main entry point: called when a user sends a draft.

        Verifies the sent message and creates a calendar invite if confirmed.

        Returns:
            dict with action taken ("sent_invite", "updated_invite", "skipped", "error")
        """
        # 1. Load the pending invite from the draft
        pending_invite = await self._get_pending_invite(draft_reply_id)
        if not pending_invite:
            return {"action": "skipped", "reason": "No pending invite for this draft"}

        # 2. Verify against the sent message
        verification = await self._verify(
            sent_message_body=sent_message_body,
            sender_email=sender_email,
            pending_invite=pending_invite,
            thread_messages=thread_messages,
        )

        action = verification.get("action", "skip")

        if action == "skip":
            logger.info(
                "Invite skipped for draft %s: %s", draft_reply_id, verification.get("reason")
            )
            return {"action": "skipped", "reason": verification.get("reason", "")}

        # 3. Resolve final event details
        if action == "update":
            # Apply updates from verification
            if verification.get("updated_event_summary"):
                pending_invite["title"] = verification["updated_event_summary"]
            if verification.get("updated_event_start"):
                pending_invite["start"] = verification["updated_event_start"]
            if verification.get("updated_event_end"):
                pending_invite["end"] = verification["updated_event_end"]
            if verification.get("updated_attendees"):
                pending_invite["attendees"] = verification["updated_attendees"]
            if verification.get("updated_location"):
                pending_invite["location"] = verification["updated_location"]

        # 4. Create the calendar event
        try:
            from src.application.dto import CreateEventDTO

            start_dt = datetime.fromisoformat(pending_invite["start"])
            end_dt = datetime.fromisoformat(pending_invite["end"])

            dto = CreateEventDTO(
                title=pending_invite.get("title", "Meeting"),
                start_time=start_dt,
                end_time=end_dt,
                description=f"Scheduled via CalendarAgent draft",
                location=pending_invite.get("location", ""),
                attendee_emails=pending_invite.get("attendees", []),
            )
            event = await self._calendar.create_event(user_id, dto)
            logger.info(
                "Calendar invite created for user %s: '%s' at %s",
                user_id,
                dto.title,
                dto.start_time,
            )
            return {
                "action": "sent_invite" if action == "send" else "updated_invite",
                "event_id": getattr(event, "id", ""),
                "title": dto.title,
                "start": dto.start_time.isoformat(),
            }
        except Exception as e:
            logger.error("Failed to create calendar invite: %s", e)
            return {"action": "error", "reason": str(e)}

    async def _verify(
        self,
        sent_message_body: str,
        sender_email: str,
        pending_invite: dict,
        thread_messages: list[dict],
    ) -> dict:
        """Call LLM to verify the sent message against the pending invite."""
        if not self._llm:
            # Conservative fallback: always skip if LLM unavailable
            return {"action": "skip", "reason": "LLM unavailable"}

        thread_section = ""
        if thread_messages:
            thread_section = ""
            for msg in thread_messages[-5:]:
                date_line = f" ({msg.get('date', '')})" if msg.get("date") else ""
                thread_section += (
                    f"From: {msg.get('sender', 'unknown')}{date_line}\n"
                    f"{msg.get('body', '')[:600]}\n\n"
                )

        attendees_str = ", ".join(pending_invite.get("attendees", []))

        user_content = _VERIFICATION_USER.format(
            summary=pending_invite.get("title", "Meeting"),
            start=pending_invite.get("start", ""),
            end=pending_invite.get("end", ""),
            attendees=attendees_str or "(not specified)",
            location=pending_invite.get("location", "(none)"),
            thread_section=thread_section or "(no thread history)",
            sender=sender_email,
            sent_body=sent_message_body[:1000],
        )

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _VERIFICATION_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                max_tokens=400,
            )
            text = (response if isinstance(response, str) else response.get("content", "")).strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            return data
        except Exception as e:
            logger.warning("Invite verification LLM call failed: %s", e)
            return {"action": "skip", "reason": f"verification error: {e}"}

    async def _get_pending_invite(self, draft_reply_id: uuid.UUID) -> dict | None:
        """Load the pending invite proposal from the draft record."""
        if not self._db:
            return None
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import DraftReplyModel

            async with self._db() as session:
                result = await session.execute(
                    select(DraftReplyModel).where(
                        DraftReplyModel.id == draft_reply_id
                    )
                )
                record = result.scalars().first()
                if record and record.pending_invite_json:
                    return json.loads(record.pending_invite_json)
        except Exception as e:
            logger.warning("Could not load pending invite: %s", e)
        return None
