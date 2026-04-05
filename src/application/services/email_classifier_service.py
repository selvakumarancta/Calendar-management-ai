"""
Email Classifier Service — LLM-powered intent classification for emails.

Determines whether an email needs a draft reply and extracts scheduling details.
Replaces the pure-regex approach with a deep LLM call that handles nuance:
  - Detects cold sales outreach (is_sales_email) and filters it out
  - Reads thread history to detect already-resolved conversations
  - Extracts proposed_times, participants, and duration_minutes
  - Returns a structured ClassificationResult

Uses the fast/cheap model (gpt-4o-mini or claude-haiku) for each call.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from src.domain.entities.email_message import (
    ClassificationResult,
    EmailCategory,
    EmailMessage,
    SuggestionPriority,
    ThreadMessage,
)

logger = logging.getLogger("calendar_agent.email_classifier")

# ---------------------------------------------------------------------------
# Fast deterministic pre-filter — zero LLM cost for clear non-actionable cases
# ---------------------------------------------------------------------------

_AUTOMATED_PATTERNS = [
    re.compile(r"no.?reply@", re.I),
    re.compile(r"noreply@", re.I),
    re.compile(r"notifications?@", re.I),
    re.compile(r"digest@", re.I),
    re.compile(r"newsletter@", re.I),
    re.compile(r"unsubscribe", re.I),
    re.compile(r"you['']?re (invited|registered|signed up)", re.I),
    re.compile(r"booking confirmation from (calendly|cal\.com|zoom)", re.I),
    re.compile(r"your (appointment|reservation|booking) (is confirmed|has been)", re.I),
]

_CALENDAR_NOTIFICATION_PATTERNS = [
    re.compile(r"has invited you to the following event", re.I),
    re.compile(r"going:\s*(yes|no|maybe)", re.I),
    re.compile(r"@google\.com.*\(invitation\)", re.I),
    re.compile(r"calendar event (updated|cancelled|created)", re.I),
    re.compile(r"^(accepted|declined|tentative):\s+", re.I),
]

_CLASSIFIER_SYSTEM_PROMPT = """You are an email scheduling classifier. Analyze incoming emails and decide:
1. Does this email require writing a draft reply to schedule/confirm/handle a meeting?
2. Is it cold sales outreach to ignore?

Return ONLY a JSON object matching this exact schema:
{{
  "needs_draft": boolean,
  "confidence": float 0.0-1.0,
  "category": one of "meeting_request|meeting_reschedule|meeting_cancellation|task_assignment|deadline_reminder|appointment|event_invitation|follow_up|non_actionable",
  "summary": "one-line summary of why a draft is/isn't needed",
  "proposed_times": ["list of times mentioned, e.g. 'Monday 3pm', 'Apr 10 at 2:00 PM'"],
  "participants": ["email or name of other participants"],
  "duration_minutes": integer or null,
  "is_sales_email": boolean,
  "already_resolved": boolean
}}

needs_draft = true examples:
- Someone personally requests a meeting (not mass email)
- Someone proposes specific times for a meeting
- Someone needs to reschedule (need to reply with alternatives)
- First-time scheduling request from a known/individual contact

needs_draft = false examples:
- Automated calendar notifications (Google Calendar, Outlook invites)
- Booking confirmations from Calendly/Cal.com (meeting already booked)
- Group announcements to many people ("Hi team", "Hi founders")
- User is only CC'd, not the primary recipient
- Newsletter, digest, product update emails
- Multi-day events (conferences, retreats, summits)
- Thread is already fully resolved (time confirmed, no further action needed)

is_sales_email = true when:
- Unsolicited cold outreach (no prior relationship)
- Sales pitches, product demos, partnership proposals
- Investor/VC cold intros, recruiting cold emails
- Stranger trying to get a meeting without prior context
NOTE: Do NOT flag replies to the user's own outreach, or emails from known contacts.

already_resolved = true when thread history shows a time was agreed upon and no further scheduling action is needed.

Today's date: {today}"""

_CLASSIFIER_USER_PROMPT = """Classify the following email for scheduling intent.

{thread_section}

LATEST MESSAGE (classify this):
From: {sender}
To: {recipient}
CC: {cc}
Subject: {subject}
Date: {date}

Body:
{body}"""


class EmailClassifierService:
    """
    LLM-powered email intent classifier.

    Pipeline:
      1. Fast deterministic pre-filter (no LLM cost)
      2. Full LLM classification with thread context
    """

    def __init__(self, llm_adapter: Any = None) -> None:
        self._llm = llm_adapter

    def _is_obviously_non_actionable(self, email: EmailMessage) -> bool:
        """Fast regex pre-check — returns True if definitely not actionable."""
        sender = email.sender_email.lower()
        text = f"{email.subject} {email.body_text[:300]}"

        for pattern in _AUTOMATED_PATTERNS:
            if pattern.search(sender) or pattern.search(email.subject):
                return True

        for pattern in _CALENDAR_NOTIFICATION_PATTERNS:
            if pattern.search(text):
                return True

        return False

    async def classify(
        self,
        email: EmailMessage,
        thread_messages: list[ThreadMessage] | None = None,
        user_email: str = "",
    ) -> ClassificationResult:
        """Classify an email for scheduling intent.

        Args:
            email: The email to classify.
            thread_messages: Full thread history for context (oldest-first).
            user_email: The user's own email (to identify their messages in thread).

        Returns:
            ClassificationResult with needs_draft, category, is_sales_email, etc.
        """
        # Step 1: fast pre-filter
        if self._is_obviously_non_actionable(email):
            logger.debug(
                "Pre-filter: non-actionable '%s' from %s",
                email.subject,
                email.sender_email,
            )
            return ClassificationResult(
                needs_draft=False,
                confidence=0.95,
                category=EmailCategory.NON_ACTIONABLE,
                summary="Automated notification or booking confirmation",
            )

        # Step 2: LLM classification
        if self._llm:
            try:
                return await self._llm_classify(
                    email, thread_messages or [], user_email
                )
            except Exception as e:
                logger.warning(
                    "LLM classifier failed for '%s': %s — falling back to heuristic",
                    email.subject,
                    e,
                )

        # Fallback: simple heuristic
        return self._heuristic_classify(email)

    async def _llm_classify(
        self,
        email: EmailMessage,
        thread_messages: list[ThreadMessage],
        user_email: str,
    ) -> ClassificationResult:
        """Full LLM-based classification with thread context."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d %A")

        # Build thread history section (oldest-first, max 5 prior messages)
        thread_section = ""
        prior_messages = [m for m in thread_messages if not m.is_from_user][-5:]
        if prior_messages:
            thread_section = "--- Thread history (oldest first) ---\n"
            for msg in prior_messages:
                date_line = f" ({msg.date})" if msg.date else ""
                thread_section += (
                    f"From: {msg.sender}{date_line}\n" f"{msg.body[:800]}\n\n"
                )
            thread_section += "--- End of thread history ---\n\n"

        system = _CLASSIFIER_SYSTEM_PROMPT.format(today=today)
        user_content = _CLASSIFIER_USER_PROMPT.format(
            thread_section=thread_section,
            sender=f"{email.sender_name} <{email.sender_email}>",
            recipient=", ".join(email.recipients[:3]),
            cc=", ".join(email.cc[:3]),
            subject=email.subject,
            date=email.received_at.strftime("%Y-%m-%d %H:%M UTC"),
            body=email.body_text[:1500],
        )

        response = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=512,
        )

        # Parse response
        text = response if isinstance(response, str) else response.get("content", "")
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(text.strip())

        category_str = data.get("category", "non_actionable")
        try:
            category = EmailCategory(category_str)
        except ValueError:
            category = EmailCategory.NON_ACTIONABLE

        duration_raw = data.get("duration_minutes")
        duration_minutes: int | None = None
        try:
            duration_minutes = int(duration_raw) if duration_raw is not None else None
        except (TypeError, ValueError):
            duration_minutes = None

        return ClassificationResult(
            needs_draft=bool(data.get("needs_draft", False)),
            confidence=float(data.get("confidence", 0.5)),
            category=category,
            summary=str(data.get("summary", "")),
            proposed_times=list(data.get("proposed_times") or []),
            participants=list(data.get("participants") or []),
            duration_minutes=duration_minutes,
            is_sales_email=bool(data.get("is_sales_email", False)),
            already_resolved=bool(data.get("already_resolved", False)),
        )

    def _heuristic_classify(self, email: EmailMessage) -> ClassificationResult:
        """Simple regex-based fallback when LLM is unavailable."""
        subject_body = f"{email.subject} {email.body_text[:500]}"

        meeting_keywords = re.compile(
            r"\b(meeting|call|sync|standup|catch.?up|appointment|schedule|availability|"
            r"free|block|invite|1.?on.?1|one.?on.?one|zoom|teams|meet)\b",
            re.I,
        )
        if not meeting_keywords.search(subject_body):
            return ClassificationResult(
                needs_draft=False,
                confidence=0.6,
                category=EmailCategory.NON_ACTIONABLE,
                summary="No scheduling keywords detected",
            )

        cancellation = re.search(r"\b(cancel|cancelled|canceled)\b", subject_body, re.I)
        if cancellation:
            return ClassificationResult(
                needs_draft=True,
                confidence=0.75,
                category=EmailCategory.MEETING_CANCELLATION,
                summary=f"Cancellation detected: {email.subject}",
            )

        return ClassificationResult(
            needs_draft=True,
            confidence=0.65,
            category=EmailCategory.MEETING_REQUEST,
            summary=f"Possible scheduling request: {email.subject}",
        )
