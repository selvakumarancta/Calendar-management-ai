"""
Draft Composer Service — generates email reply drafts with proposed meeting times.

This is the core AI service that:
1. Reads the full email thread for context
2. Checks the user's calendar for availability
3. Composes a natural-sounding reply proposing concrete times
4. Creates the draft in Gmail for user review
5. Optionally sends immediately in autopilot mode (1:1 meetings only)

The draft body is:
- Thread-aware (never re-suggests declined times)
- Timezone-correct (times in user's local timezone)
- Personalized (uses the user's email style guide)
- Branded with "Sent by CalendarAgent" footer (if branding_enabled)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.domain.entities.email_message import (
    ClassificationResult,
    DraftReply,
    DraftStatus,
    EmailMessage,
    SchedulingLink,
    ThreadMessage,
)

logger = logging.getLogger("calendar_agent.draft_composer")

# ---------------------------------------------------------------------------
# Default guides (used when user hasn't completed onboarding yet)
# ---------------------------------------------------------------------------

DEFAULT_SCHEDULING_PREFERENCES = """
- Prefer morning slots (9am–12pm) for meetings
- Keep Fridays light — avoid scheduling new meetings after 3pm on Fridays
- Always buffer 15 minutes between back-to-back meetings
- Prefer 30-minute calls for first-time connections; 60 minutes for working sessions
- Avoid early Monday mornings (before 10am)
"""

DEFAULT_EMAIL_STYLE = """
- Warm, concise, professional tone
- Avoid corporate jargon ("per my last email", "as per")
- Short sentences — get to the point quickly
- Friendly sign-off ("Best," or "Thanks,")
- Do not use passive-aggressive language
"""

_DRAFT_SYSTEM_PROMPT = """You are a scheduling assistant that composes email reply drafts.

Your job: read the thread, check the calendar, and write a natural reply with proposed meeting times.

## Scheduling Preferences
{scheduling_prefs}

## Email Style Guide
{email_style}

## Rules
1. NEVER re-suggest a time that was already declined or said not to work in the thread.
2. If the thread is already resolved (time confirmed), do NOT compose a draft — respond with {{"skip": true}}.
3. Check for conflicts before proposing times.
4. Times MUST be in the user's timezone ({user_timezone} offset) — include the offset in ISO format.
5. For group meetings (3+ participants), always produce a draft (not autopilot).
6. Keep the draft warm, concise, and human-sounding.
7. Preserve CC recipients from the thread in your reply.
8. You are composing on behalf of: {user_email}

## Today
{today}

## Available Calendar Data
{calendar_summary}
"""

_DRAFT_USER_PROMPT = """Compose a scheduling reply for this email.

Thread (oldest first):
{thread_section}

LATEST EMAIL TO REPLY TO:
From: {sender}
To: {recipients}
CC: {cc}
Subject: {subject}
Body:
{body}

Classification:
{classification_json}

Instructions:
- Reply to: {reply_to}
- Proposed times already mentioned in thread (do NOT re-suggest): {declined_times}
- Draft should propose {num_slots} concrete time slots based on the user's calendar availability.
- If meeting duration is unclear, use {duration_minutes} minutes.
- Sign the reply as {user_display_name}.

Return JSON only:
{{
  "skip": false,
  "reply_body": "full email body text",
  "reply_subject": "Re: {subject_short}",
  "reply_cc": "cc emails if any",
  "proposed_windows": [{{"date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM"}}],
  "duration_minutes": integer,
  "event_summary": "short title for the calendar event",
  "is_confirmation": false,
  "pending_invite": null or {{"title": str, "start": "ISO8601", "end": "ISO8601", "attendees": [str], "location": str}}
}}

If the thread is already resolved, return: {{"skip": true}}
"""


class DraftComposerService:
    """
    Composes draft email replies with proposed meeting times.
    Creates the draft in Gmail via the email provider adapter.
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

    async def compose_and_create_draft(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
        user_id: uuid.UUID,
        user_email: str,
        user_timezone: str,
        email_provider: Any,
        email_style_guide: str = "",
        scheduling_preferences_guide: str = "",
        autopilot_enabled: bool = False,
        org_id: uuid.UUID | None = None,
    ) -> DraftReply | None:
        """
        Main entry point: compose a draft reply and create it in Gmail.

        Returns:
            DraftReply domain object if a draft was created, None if skipped.
        """
        if not self._llm:
            logger.warning("No LLM adapter — cannot compose draft")
            return None

        # 1. Fetch calendar availability for the next 14 days
        calendar_summary = await self._get_calendar_summary(user_id, user_timezone)

        # 2. Build thread summary (declined times)
        declined_times = self._extract_declined_times(email.thread_messages)

        # 3. Ask LLM to compose the draft
        draft_data = await self._compose_draft_llm(
            email=email,
            classification=classification,
            user_email=user_email,
            user_timezone=user_timezone,
            calendar_summary=calendar_summary,
            declined_times=declined_times,
            email_style_guide=email_style_guide or DEFAULT_EMAIL_STYLE,
            scheduling_preferences_guide=scheduling_preferences_guide or DEFAULT_SCHEDULING_PREFERENCES,
        )

        if not draft_data or draft_data.get("skip"):
            logger.info(
                "Draft composer: skipping '%s' (thread already resolved)", email.subject
            )
            return None

        reply_body = draft_data.get("reply_body", "")
        if not reply_body:
            return None

        # 4. Add branding footer
        reply_body = self._add_footer(reply_body)

        # 5. Determine if this is a group meeting
        all_participants = list(set(
            email.recipients + email.cc + [email.sender_email]
        ))
        # Exclude the user's own email
        other_participants = [p for p in all_participants if p.lower() != user_email.lower()]
        is_group = len(other_participants) >= 2

        # 6. Create draft or send immediately (autopilot for 1:1 only)
        reply_subject = draft_data.get("reply_subject", f"Re: {email.subject}")
        reply_to = email.sender_email
        reply_cc = draft_data.get("reply_cc", "")

        draft_status = DraftStatus.PENDING
        draft_provider_id = ""

        if autopilot_enabled and not is_group and not draft_data.get("is_confirmation"):
            # Autopilot: send directly for 1:1 scheduling
            sent_id = await email_provider.send_email_reply(
                user_id=user_id,
                thread_id=email.thread_id,
                to=reply_to,
                subject=reply_subject,
                body=reply_body,
                cc=reply_cc,
            ) if hasattr(email_provider, "send_email_reply") else ""

            if not sent_id:
                # Fallback: create draft if send failed
                draft_provider_id = await email_provider.create_draft_reply(
                    user_id=user_id,
                    thread_id=email.thread_id,
                    to=reply_to,
                    subject=reply_subject,
                    body=reply_body,
                    cc=reply_cc,
                )
            else:
                draft_provider_id = f"sent:{sent_id}"
                draft_status = DraftStatus.AUTOPILOT_SENT
        else:
            # Normal: create Gmail draft for user review
            draft_provider_id = await email_provider.create_draft_reply(
                user_id=user_id,
                thread_id=email.thread_id,
                to=reply_to,
                subject=reply_subject,
                body=reply_body,
                cc=reply_cc,
            )

        if not draft_provider_id:
            logger.error(
                "Failed to create draft in Gmail for user %s thread %s",
                user_id,
                email.thread_id,
            )
            return None

        draft = DraftReply(
            user_id=user_id,
            org_id=org_id,
            email_provider_id=email.provider_message_id,
            thread_id=email.thread_id,
            email_subject=email.subject,
            email_sender=email.sender_email,
            email_received_at=email.received_at,
            draft_provider_id=draft_provider_id,
            reply_to=reply_to,
            reply_cc=reply_cc,
            reply_subject=reply_subject,
            reply_body=reply_body,
            proposed_windows=draft_data.get("proposed_windows", []),
            duration_minutes=int(draft_data.get("duration_minutes", 30)),
            event_summary=draft_data.get("event_summary", email.subject),
            pending_invite=draft_data.get("pending_invite"),
            status=draft_status,
            is_group_meeting=is_group,
            autopilot_eligible=autopilot_enabled and not is_group,
        )

        # 7. Persist the draft
        await self._save_draft(draft)

        logger.info(
            "Draft created for user %s: '%s' → Gmail draft %s (autopilot=%s)",
            user_id,
            email.subject,
            draft_provider_id[:12],
            draft_status == DraftStatus.AUTOPILOT_SENT,
        )
        return draft

    async def _compose_draft_llm(
        self,
        email: EmailMessage,
        classification: ClassificationResult,
        user_email: str,
        user_timezone: str,
        calendar_summary: str,
        declined_times: list[str],
        email_style_guide: str,
        scheduling_preferences_guide: str,
    ) -> dict | None:
        """Call LLM to generate the draft reply body and metadata."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d %A")
        user_display_name = user_email.split("@")[0].replace(".", " ").title()

        # Build thread section
        thread_section = ""
        if email.thread_messages:
            for msg in email.thread_messages[-6:]:  # Last 6 messages for context
                who = "(you)" if msg.is_from_user else msg.sender
                date_part = f" [{msg.date}]" if msg.date else ""
                thread_section += f"--- {who}{date_part} ---\n{msg.body[:600]}\n\n"

        system = _DRAFT_SYSTEM_PROMPT.format(
            scheduling_prefs=scheduling_preferences_guide,
            email_style=email_style_guide,
            user_timezone=user_timezone,
            user_email=user_email,
            today=today,
            calendar_summary=calendar_summary,
        )

        user_content = _DRAFT_USER_PROMPT.format(
            thread_section=thread_section or "(no prior thread history)",
            sender=f"{email.sender_name} <{email.sender_email}>",
            recipients=", ".join(email.recipients[:3]),
            cc=", ".join(email.cc[:3]),
            subject=email.subject,
            body=email.body_text[:1200],
            classification_json=json.dumps(
                {
                    "category": classification.category.value,
                    "summary": classification.summary,
                    "proposed_times": classification.proposed_times,
                    "participants": classification.participants,
                    "duration_minutes": classification.duration_minutes,
                },
                indent=2,
            ),
            reply_to=email.sender_email,
            declined_times=", ".join(declined_times) if declined_times else "none",
            num_slots=3,
            duration_minutes=classification.duration_minutes or 30,
            subject_short=email.subject[:60],
            user_display_name=user_display_name,
        )

        response = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=1500,
        )

        text = response if isinstance(response, str) else response.get("content", "")
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            # Try to extract JSON from within text
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Could not parse draft composer response as JSON")
            return None

    async def _get_calendar_summary(
        self, user_id: uuid.UUID, user_timezone: str
    ) -> str:
        """Get a compact summary of upcoming calendar events for the next 14 days."""
        if not self._calendar:
            return "Calendar not connected."

        try:
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=14)
            events = await self._calendar.list_events(user_id, now, end)
            if not events:
                return "No events in the next 14 days — calendar is clear."

            lines = [f"Upcoming events (next 14 days, {user_timezone}):"]
            for ev in events[:20]:  # Cap at 20 for token efficiency
                day = ev.start_time.strftime("%A %b %-d")
                t_start = ev.start_time.strftime("%H:%M")
                t_end = ev.end_time.strftime("%H:%M")
                lines.append(f"  • {day} {t_start}-{t_end}: {ev.title}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Could not fetch calendar summary: %s", e)
            return "Calendar data unavailable."

    def _extract_declined_times(self, thread_messages: list[ThreadMessage]) -> list[str]:
        """Extract times that were already declined in the thread."""
        declined: list[str] = []
        decline_patterns = [
            re.compile(r"(can['']?t|cannot|won['']?t|doesn['']?t work|not available).{0,60}(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.I),
            re.compile(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?).{0,40}(doesn['']?t work|not available|can['']?t)", re.I),
        ]
        for msg in thread_messages:
            for pattern in decline_patterns:
                matches = pattern.findall(msg.body)
                for match in matches:
                    time_part = max(match, key=len)
                    if time_part:
                        declined.append(time_part.strip())
        return list(set(declined))[:5]  # Deduplicate and cap

    def _add_footer(self, body: str) -> str:
        """Add a discreet CalendarAgent branding footer to the draft body."""
        footer = "\n\n---\n_Drafted by CalendarAgent_"
        return body + footer

    async def _save_draft(self, draft: DraftReply) -> None:
        """Persist the draft reply to the database."""
        if not self._db:
            return
        try:
            from src.infrastructure.persistence.email_models import DraftReplyModel

            async with self._db() as session:
                model = DraftReplyModel(
                    id=draft.id,
                    user_id=draft.user_id,
                    org_id=draft.org_id,
                    email_provider_id=draft.email_provider_id,
                    thread_id=draft.thread_id,
                    email_subject=draft.email_subject,
                    email_sender=draft.email_sender,
                    email_received_at=draft.email_received_at,
                    draft_provider_id=draft.draft_provider_id,
                    reply_to=draft.reply_to,
                    reply_cc=draft.reply_cc,
                    reply_subject=draft.reply_subject,
                    reply_body=draft.reply_body,
                    proposed_windows_json=json.dumps(draft.proposed_windows),
                    duration_minutes=draft.duration_minutes,
                    event_summary=draft.event_summary,
                    pending_invite_json=json.dumps(draft.pending_invite) if draft.pending_invite else None,
                    status=draft.status.value,
                    is_group_meeting=draft.is_group_meeting,
                    autopilot_eligible=draft.autopilot_eligible,
                )
                session.add(model)
                await session.commit()
        except Exception as e:
            logger.error("Failed to save draft to DB: %s", e)
