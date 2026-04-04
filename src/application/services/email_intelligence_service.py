"""
Email Intelligence Service — analyzes emails and creates calendar suggestions.
This is the core application service that orchestrates:
1. Fetching emails from Gmail/Outlook providers
2. LLM-powered analysis to detect meetings, tasks, appointments
3. Calendar availability checking
4. Creating schedule suggestions for user approval or auto-scheduling
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.domain.entities.email_message import (
    EmailAnalysis,
    EmailCategory,
    EmailMessage,
    EmailScanResult,
    ScheduleSuggestion,
    SuggestionPriority,
    SuggestionStatus,
)

logger = logging.getLogger("calendar_agent.email_intelligence")

# ---------------------------------------------------------------------------
# Deterministic shortcuts — bypass LLM for obvious patterns
# ---------------------------------------------------------------------------

_MEETING_PATTERNS = [
    re.compile(r"(?:zoom|teams|meet|webex)\s+(?:meeting|call)", re.I),
    re.compile(r"(?:join|attend)\s+(?:the\s+)?(?:meeting|call|session)", re.I),
    re.compile(r"(?:invite|invitation)\s+(?:to|for)\s+", re.I),
    re.compile(
        r"(?:scheduled|rescheduled)\s+(?:a\s+)?(?:meeting|call|appointment)", re.I
    ),
    re.compile(r"(?:standup|stand-up|sync|1:1|one-on-one)", re.I),
    re.compile(r"let(?:'s| us)\s+(?:meet|schedule|set up|arrange)", re.I),
    re.compile(r"(?:please|can you)\s+(?:schedule|book|arrange|set up)", re.I),
    # Broader patterns — catch simple meeting subjects
    re.compile(r"^meeting\b", re.I),
    re.compile(r"\bmeeting\s+(?:with|about|for|on|at|re:)", re.I),
    re.compile(r"(?:team|project|client|weekly|daily|monthly)\s+meeting", re.I),
    re.compile(r"\binvitation\b", re.I),
    re.compile(r"\bnotification:\s+.*@", re.I),  # Google Calendar notifications
    re.compile(r"\bcalendar\s+(?:event|invite|notification)", re.I),
    re.compile(r"\b(?:accepted|declined|tentative):\s+", re.I),  # RSVP replies
    re.compile(r"(?:meeting|event|call)\s+(?:reminder|update|changed)", re.I),
    re.compile(r"\bconference\b|\bwebinar\b|\bdemo\b|\binterview\b", re.I),
    re.compile(r"(?:appointment|booking)\s+(?:confirmation|reminder)", re.I),
]

_CANCEL_PATTERNS = [
    re.compile(
        r"(?:cancel|cancelled|canceled)\s+(?:the\s+)?(?:meeting|call|event)", re.I
    ),
    re.compile(r"(?:meeting|call|event)\s+(?:has been\s+)?(?:cancel)", re.I),
]

_DEADLINE_PATTERNS = [
    re.compile(r"(?:deadline|due date|due by|submit by|deliver by)", re.I),
    re.compile(r"(?:urgent|asap|immediately|by end of day|by eod|by cob)", re.I),
]

_TIME_PATTERNS = [
    re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm|AM|PM)", re.I),
    re.compile(r"(\d{1,2})\s*(am|pm|AM|PM)", re.I),
    re.compile(r"at\s+(\d{1,2}(?::\d{2})?)\s*(?:am|pm|AM|PM)?", re.I),
]

_DATE_PATTERNS = [
    re.compile(r"(today|tomorrow|day after tomorrow)", re.I),
    re.compile(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", re.I),
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", re.I),
    re.compile(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})", re.I
    ),
]

# ---------------------------------------------------------------------------
# LLM prompt for email analysis (token-efficient)
# ---------------------------------------------------------------------------

_ANALYSIS_PROMPT = """Analyze this email for scheduling actions. Return JSON only.

Email:
Subject: {subject}
From: {sender}
Date: {date}
Body (first 500 chars): {body}

Return this exact JSON structure:
{{
  "is_actionable": true/false,
  "category": "meeting_request|meeting_reschedule|meeting_cancellation|task_assignment|deadline_reminder|appointment|event_invitation|follow_up|non_actionable",
  "confidence": 0.0-1.0,
  "title": "suggested calendar event title",
  "date": "YYYY-MM-DD or relative like 'tomorrow'",
  "time": "HH:MM in 24h format",
  "duration_minutes": 30,
  "location": "location or meeting link if mentioned",
  "attendees": ["email1@example.com"],
  "summary": "one-line summary",
  "action_required": "what action needed",
  "priority": "high|medium|low"
}}

Rules:
- Only mark as actionable if there's a clear scheduling action needed
- Extract dates/times relative to {today}
- For meetings without specific time, suggest during working hours
- Be conservative with confidence scores"""


class EmailIntelligenceService:
    """Orchestrates email scanning, LLM analysis, and calendar suggestions."""

    def __init__(
        self,
        llm_adapter: Any = None,
        calendar_adapter: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._llm = llm_adapter
        self._calendar = calendar_adapter
        self._db_session_factory = db_session_factory

    async def scan_user_emails(
        self,
        user_id: uuid.UUID,
        email_provider: Any,
        provider_name: str,
        org_id: uuid.UUID | None = None,
        since_hours: int = 24,
        max_emails: int = 30,
    ) -> EmailScanResult:
        """Scan a user's inbox and create schedule suggestions.

        This is the main entry point for email intelligence.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        result = EmailScanResult(user_id=user_id, provider=provider_name)

        try:
            # 1. Fetch recent emails
            emails = await email_provider.list_recent_emails(
                user_id=user_id,
                since=since,
                max_results=max_emails,
            )
            result.emails_scanned = len(emails)
            logger.info("Scanned %d emails for user %s", len(emails), user_id)

            if not emails:
                return result

            # 2. Filter already-processed emails
            emails = await self._filter_processed(user_id, emails)

            # 3. Analyze each email and store it
            for email in emails:
                try:
                    analysis = await self.analyze_email(email)
                    suggestion = None

                    if analysis.is_actionable:
                        result.actionable_found += 1

                        # 4. Create a schedule suggestion
                        suggestion = await self._create_suggestion(
                            email=email,
                            analysis=analysis,
                            user_id=user_id,
                            org_id=org_id,
                        )
                        if suggestion:
                            result.suggestions_created += 1

                    # 5. Store the scanned email for browsing
                    await self._save_scanned_email(
                        email=email,
                        analysis=analysis,
                        user_id=user_id,
                        suggestion_id=suggestion.id if suggestion else None,
                    )

                except Exception as e:
                    logger.warning("Failed to analyze email %s: %s", email.subject, e)
                    result.errors.append(f"Email '{email.subject}': {e}")

            # 5. Log the scan
            await self._log_scan(result, org_id)

        except Exception as e:
            logger.error("Email scan failed for user %s: %s", user_id, e)
            result.errors.append(str(e))

        return result

    async def analyze_email(self, email: EmailMessage) -> EmailAnalysis:
        """Analyze a single email for scheduling intent.

        Uses deterministic regex patterns first (fast + free),
        falls back to LLM for ambiguous cases.
        """
        # Try deterministic analysis first
        analysis = self._deterministic_analysis(email)
        if analysis and analysis.confidence >= 0.6:
            logger.debug(
                "Deterministic match for '%s': %s", email.subject, analysis.category
            )
            return analysis

        # Fall back to LLM analysis
        if self._llm:
            try:
                llm_analysis = await self._llm_analysis(email)
                if llm_analysis:
                    return llm_analysis
            except Exception as e:
                logger.warning("LLM analysis failed for '%s': %s", email.subject, e)

        # Return deterministic result (even if low confidence) or non-actionable
        return analysis or EmailAnalysis(
            email_id=email.id,
            category=EmailCategory.NON_ACTIONABLE,
            is_actionable=False,
        )

    def _deterministic_analysis(self, email: EmailMessage) -> EmailAnalysis | None:
        """Fast regex-based analysis — no LLM cost."""
        text = f"{email.subject} {email.body_text[:500]}"

        # Check Google Calendar notification format first
        # "Notification: TestEvent @ Wed Apr 1, 2026 12:30am - 1:30am (IST)"
        gcal_match = re.match(
            r"Notification:\s+(.+?)\s+@\s+(.+?)(?:\s+\((.+?)\))?$",
            email.subject,
        )
        if gcal_match:
            event_title = gcal_match.group(1)
            event_time = gcal_match.group(2)
            time_str = self._extract_time(event_time)
            date_str = self._extract_date(event_time)
            return EmailAnalysis(
                email_id=email.id,
                category=EmailCategory.EVENT_INVITATION,
                confidence=0.95,
                suggested_title=event_title,
                suggested_time=time_str,
                suggested_date=date_str,
                suggested_duration_minutes=60,
                suggested_attendees=[email.sender_email] if email.sender_email else [],
                summary=f"Calendar notification: {event_title}",
                action_required="Add event to calendar",
                urgency=SuggestionPriority.HIGH,
                is_actionable=True,
            )

        # Check "Accepted/Declined/Tentative:" RSVP pattern
        rsvp_match = re.match(
            r"(Accepted|Declined|Tentative):\s+(.+)", email.subject, re.I
        )
        if rsvp_match:
            return EmailAnalysis(
                email_id=email.id,
                category=EmailCategory.MEETING_RESCHEDULE,
                confidence=0.85,
                suggested_title=rsvp_match.group(2),
                summary=f"RSVP {rsvp_match.group(1)}: {rsvp_match.group(2)}",
                action_required="Update calendar event",
                urgency=SuggestionPriority.MEDIUM,
                is_actionable=True,
            )

        # Check cancellation
        for pattern in _CANCEL_PATTERNS:
            if pattern.search(text):
                return EmailAnalysis(
                    email_id=email.id,
                    category=EmailCategory.MEETING_CANCELLATION,
                    confidence=0.85,
                    suggested_title=f"CANCELLED: {email.subject}",
                    summary=f"Meeting cancellation from {email.sender_name or email.sender_email}",
                    action_required="Remove cancelled event from calendar",
                    urgency=SuggestionPriority.HIGH,
                    is_actionable=True,
                )

        # Check meeting patterns
        for pattern in _MEETING_PATTERNS:
            if pattern.search(text):
                # Try to extract time/date
                time_str = self._extract_time(text)
                date_str = self._extract_date(text)
                return EmailAnalysis(
                    email_id=email.id,
                    category=EmailCategory.MEETING_REQUEST,
                    confidence=0.8,
                    suggested_title=email.subject,
                    suggested_time=time_str,
                    suggested_date=date_str,
                    suggested_duration_minutes=30,
                    suggested_attendees=(
                        [email.sender_email] if email.sender_email else []
                    ),
                    summary=f"Meeting request from {email.sender_name or email.sender_email}",
                    action_required="Schedule meeting",
                    urgency=SuggestionPriority.MEDIUM,
                    is_actionable=True,
                )

        # Check deadline patterns
        for pattern in _DEADLINE_PATTERNS:
            if pattern.search(text):
                date_str = self._extract_date(text)
                return EmailAnalysis(
                    email_id=email.id,
                    category=EmailCategory.DEADLINE_REMINDER,
                    confidence=0.75,
                    suggested_title=f"Deadline: {email.subject}",
                    suggested_date=date_str,
                    summary=f"Deadline mentioned by {email.sender_name or email.sender_email}",
                    action_required="Add deadline to calendar",
                    urgency=SuggestionPriority.HIGH,
                    is_actionable=True,
                )

        return None

    async def _llm_analysis(self, email: EmailMessage) -> EmailAnalysis | None:
        """Use LLM for deeper analysis of ambiguous emails."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        prompt = _ANALYSIS_PROMPT.format(
            subject=email.subject,
            sender=f"{email.sender_name} <{email.sender_email}>",
            date=email.received_at.strftime("%Y-%m-%d %H:%M"),
            body=email.body_text[:500],
            today=today,
        )

        response = await self._llm.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": "You are a scheduling assistant. Analyze emails and return JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.1,
        )

        # Parse JSON from LLM response
        try:
            # Extract JSON from response (handle markdown code blocks)
            text = ""
            if isinstance(response, dict):
                # Anthropic returns {"content": [{"text": "..."}]} or {"content": "..."}
                content = response.get("content", "")
                if isinstance(content, list):
                    text = content[0].get("text", "") if content else ""
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)
            else:
                text = str(response)
            json_match = re.search(r"\{[^}]+\}", text, re.DOTALL)
            if not json_match:
                return None

            data = json.loads(json_match.group())

            category_map = {v.value: v for v in EmailCategory}
            priority_map = {v.value: v for v in SuggestionPriority}

            return EmailAnalysis(
                email_id=email.id,
                category=category_map.get(
                    data.get("category", ""), EmailCategory.NON_ACTIONABLE
                ),
                confidence=float(data.get("confidence", 0.0)),
                suggested_title=data.get("title", email.subject),
                suggested_date=data.get("date", ""),
                suggested_time=data.get("time", ""),
                suggested_duration_minutes=int(data.get("duration_minutes", 30)),
                suggested_location=data.get("location", ""),
                suggested_attendees=data.get("attendees", []),
                summary=data.get("summary", ""),
                action_required=data.get("action_required", ""),
                urgency=priority_map.get(
                    data.get("priority", "medium"), SuggestionPriority.MEDIUM
                ),
                is_actionable=data.get("is_actionable", False),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to parse LLM analysis JSON: %s", e)
            return None

    async def _create_suggestion(
        self,
        email: EmailMessage,
        analysis: EmailAnalysis,
        user_id: uuid.UUID,
        org_id: uuid.UUID | None = None,
    ) -> ScheduleSuggestion | None:
        """Create a schedule suggestion and check for conflicts."""
        now = datetime.now(timezone.utc)

        # Parse proposed start/end times
        proposed_start, proposed_end = self._resolve_datetime(
            date_str=analysis.suggested_date,
            time_str=analysis.suggested_time,
            duration_minutes=analysis.suggested_duration_minutes,
            reference_date=now,
        )

        suggestion = ScheduleSuggestion(
            user_id=user_id,
            org_id=org_id,
            email_provider_id=email.provider_message_id,
            email_subject=email.subject,
            email_sender=email.sender_email,
            email_received_at=email.received_at,
            email_snippet=email.body_preview,
            category=analysis.category,
            confidence=analysis.confidence,
            priority=analysis.urgency,
            title=analysis.suggested_title or email.subject,
            description=f"From email: {email.subject}\nSender: {email.sender_email}\n\n{analysis.summary}",
            proposed_start=proposed_start,
            proposed_end=proposed_end,
            location=analysis.suggested_location,
            attendees=analysis.suggested_attendees,
            status=SuggestionStatus.PENDING,
        )

        # Check calendar conflicts
        if proposed_start and proposed_end and self._calendar:
            try:
                events = await self._calendar.list_events(
                    user_id=user_id,
                    start=proposed_start,
                    end=proposed_end,
                )
                if events:
                    suggestion.has_conflict = True
                    conflict_titles = [e.title for e in events[:3]]
                    suggestion.conflict_details = (
                        f"Conflicts with: {', '.join(conflict_titles)}"
                    )

                    # Find alternative slots
                    try:
                        alt_start = proposed_start.replace(hour=9, minute=0)
                        alt_end = proposed_start.replace(hour=18, minute=0)
                        free_slots = await self._calendar.find_free_slots(
                            user_id=user_id,
                            start=alt_start,
                            end=alt_end,
                            duration_minutes=analysis.suggested_duration_minutes,
                        )
                        suggestion.alternative_slots = [
                            {"start": s.start.isoformat(), "end": s.end.isoformat()}
                            for s in free_slots[:3]
                        ]
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Calendar conflict check failed: %s", e)

        # Persist to DB
        await self._save_suggestion(suggestion)

        return suggestion

    async def get_suggestions(
        self,
        user_id: uuid.UUID,
        status: str | None = None,
        limit: int = 50,
    ) -> list[ScheduleSuggestion]:
        """Get schedule suggestions for a user."""
        if not self._db_session_factory:
            return []

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import ScheduleSuggestionModel

        async with self._db_session_factory() as session:
            query = (
                select(ScheduleSuggestionModel)
                .where(
                    ScheduleSuggestionModel.user_id == user_id,
                )
                .order_by(ScheduleSuggestionModel.created_at.desc())
                .limit(limit)
            )

            if status:
                query = query.where(ScheduleSuggestionModel.status == status)

            result = await session.execute(query)
            rows = result.scalars().all()

            return [self._model_to_suggestion(row) for row in rows]

    async def approve_suggestion(
        self,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ScheduleSuggestion | None:
        """Approve a suggestion and create a calendar event."""
        if not self._db_session_factory:
            return None

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import ScheduleSuggestionModel

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ScheduleSuggestionModel).where(
                    ScheduleSuggestionModel.id == suggestion_id,
                    ScheduleSuggestionModel.user_id == user_id,
                )
            )
            model = result.scalar_one_or_none()
            if not model:
                return None

            # Create calendar event
            if self._calendar and model.proposed_start and model.proposed_end:
                from src.domain.entities.calendar_event import Attendee, CalendarEvent

                event = CalendarEvent(
                    user_id=user_id,
                    title=model.title,
                    description=model.description,
                    location=model.location,
                    start_time=model.proposed_start,
                    end_time=model.proposed_end,
                    attendees=[
                        Attendee(email=a) for a in json.loads(model.attendees_json)
                    ],
                )
                try:
                    created = await self._calendar.create_event(user_id, event)
                    model.calendar_event_id = created.provider_event_id
                except Exception as e:
                    logger.warning("Failed to create calendar event: %s", e)

            model.status = "approved"
            model.resolved_at = datetime.now(timezone.utc)
            await session.commit()

            return self._model_to_suggestion(model)

    async def reject_suggestion(
        self,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Reject a suggestion."""
        if not self._db_session_factory:
            return False

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import ScheduleSuggestionModel

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ScheduleSuggestionModel).where(
                    ScheduleSuggestionModel.id == suggestion_id,
                    ScheduleSuggestionModel.user_id == user_id,
                )
            )
            model = result.scalar_one_or_none()
            if not model:
                return False

            model.status = "rejected"
            model.resolved_at = datetime.now(timezone.utc)
            await session.commit()
            return True

    async def get_scan_history(
        self,
        user_id: uuid.UUID,
        limit: int = 10,
    ) -> list[dict]:
        """Get scan history for a user."""
        if not self._db_session_factory:
            return []

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import EmailScanLogModel

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(EmailScanLogModel)
                .where(EmailScanLogModel.user_id == user_id)
                .order_by(EmailScanLogModel.scanned_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": str(r.id),
                    "provider": r.provider,
                    "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
                    "emails_scanned": r.emails_scanned,
                    "actionable_found": r.actionable_found,
                    "suggestions_created": r.suggestions_created,
                    "errors_count": (
                        len(json.loads(r.errors_json)) if r.errors_json else 0
                    ),
                }
                for r in rows
            ]

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _filter_processed(
        self, user_id: uuid.UUID, emails: list[EmailMessage]
    ) -> list[EmailMessage]:
        """Filter out emails that have already been scanned."""
        if not self._db_session_factory or not emails:
            return emails

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import ScannedEmailModel

        provider_ids = [e.provider_message_id for e in emails]

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ScannedEmailModel.provider_message_id).where(
                    ScannedEmailModel.user_id == user_id,
                    ScannedEmailModel.provider_message_id.in_(provider_ids),
                )
            )
            already_processed = {row[0] for row in result.all()}

        return [e for e in emails if e.provider_message_id not in already_processed]

    async def _save_suggestion(self, suggestion: ScheduleSuggestion) -> None:
        """Persist a schedule suggestion to the DB."""
        if not self._db_session_factory:
            return

        from src.infrastructure.persistence.email_models import ScheduleSuggestionModel

        async with self._db_session_factory() as session:
            model = ScheduleSuggestionModel(
                id=suggestion.id,
                user_id=suggestion.user_id,
                org_id=suggestion.org_id,
                email_provider_id=suggestion.email_provider_id,
                email_subject=suggestion.email_subject,
                email_sender=suggestion.email_sender,
                email_received_at=suggestion.email_received_at,
                email_snippet=suggestion.email_snippet,
                category=suggestion.category.value,
                confidence=suggestion.confidence,
                priority=suggestion.priority.value,
                title=suggestion.title,
                description=suggestion.description,
                proposed_start=suggestion.proposed_start,
                proposed_end=suggestion.proposed_end,
                location=suggestion.location,
                attendees_json=json.dumps(suggestion.attendees),
                status=suggestion.status.value,
                has_conflict=suggestion.has_conflict,
                conflict_details=suggestion.conflict_details,
                alternative_slots_json=json.dumps(suggestion.alternative_slots),
            )
            session.add(model)
            await session.commit()

    async def _log_scan(
        self, result: EmailScanResult, org_id: uuid.UUID | None = None
    ) -> None:
        """Log a scan run to the DB."""
        if not self._db_session_factory:
            return

        from src.infrastructure.persistence.email_models import EmailScanLogModel

        async with self._db_session_factory() as session:
            model = EmailScanLogModel(
                user_id=result.user_id,
                org_id=org_id,
                provider=result.provider,
                scanned_at=result.scanned_at,
                emails_scanned=result.emails_scanned,
                actionable_found=result.actionable_found,
                suggestions_created=result.suggestions_created,
                errors_json=json.dumps(result.errors),
            )
            session.add(model)
            await session.commit()

    async def _save_scanned_email(
        self,
        email: EmailMessage,
        analysis: EmailAnalysis,
        user_id: uuid.UUID,
        suggestion_id: uuid.UUID | None = None,
    ) -> None:
        """Persist a scanned email with its analysis result for browsing."""
        if not self._db_session_factory:
            return

        from src.infrastructure.persistence.email_models import ScannedEmailModel

        async with self._db_session_factory() as session:
            model = ScannedEmailModel(
                user_id=user_id,
                provider_message_id=email.provider_message_id,
                provider=email.provider,
                subject=email.subject,
                sender_email=email.sender_email,
                sender_name=email.sender_name,
                recipients_json=json.dumps(email.recipients),
                body_snippet=email.body_preview,
                body_text=email.body_text[:5000],  # limit body size
                received_at=email.received_at,
                thread_id=email.thread_id,
                has_attachments=email.has_attachments,
                is_read=email.is_read,
                is_actionable=analysis.is_actionable,
                analysis_category=analysis.category.value,
                analysis_confidence=analysis.confidence,
                analysis_summary=analysis.summary or analysis.action_required,
                suggestion_id=suggestion_id,
            )
            session.add(model)
            await session.commit()

    async def get_scanned_emails(
        self,
        user_id: uuid.UUID,
        actionable_only: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """Get scanned emails for a user with analysis results."""
        if not self._db_session_factory:
            return []

        from sqlalchemy import select

        from src.infrastructure.persistence.email_models import ScannedEmailModel

        async with self._db_session_factory() as session:
            query = (
                select(ScannedEmailModel)
                .where(ScannedEmailModel.user_id == user_id)
                .order_by(ScannedEmailModel.received_at.desc())
                .limit(limit)
            )
            if actionable_only:
                query = query.where(ScannedEmailModel.is_actionable.is_(True))

            result = await session.execute(query)
            rows = result.scalars().all()

            return [
                {
                    "id": str(r.id),
                    "provider_message_id": r.provider_message_id,
                    "provider": r.provider,
                    "subject": r.subject,
                    "sender_email": r.sender_email,
                    "sender_name": r.sender_name,
                    "recipients": (
                        json.loads(r.recipients_json) if r.recipients_json else []
                    ),
                    "body_snippet": r.body_snippet,
                    "body_text": r.body_text,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                    "thread_id": r.thread_id,
                    "has_attachments": r.has_attachments,
                    "is_read": r.is_read,
                    "is_actionable": r.is_actionable,
                    "analysis_category": r.analysis_category,
                    "analysis_confidence": r.analysis_confidence,
                    "analysis_summary": r.analysis_summary,
                    "suggestion_id": str(r.suggestion_id) if r.suggestion_id else None,
                    "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
                }
                for r in rows
            ]

    @staticmethod
    def _model_to_suggestion(model: Any) -> ScheduleSuggestion:
        """Convert DB model to domain entity."""
        category_map = {v.value: v for v in EmailCategory}
        priority_map = {v.value: v for v in SuggestionPriority}
        status_map = {v.value: v for v in SuggestionStatus}

        return ScheduleSuggestion(
            id=model.id,
            user_id=model.user_id,
            org_id=model.org_id,
            email_provider_id=model.email_provider_id,
            email_subject=model.email_subject,
            email_sender=model.email_sender,
            email_received_at=model.email_received_at,
            email_snippet=model.email_snippet,
            category=category_map.get(model.category, EmailCategory.MEETING_REQUEST),
            confidence=model.confidence,
            priority=priority_map.get(model.priority, SuggestionPriority.MEDIUM),
            title=model.title,
            description=model.description,
            proposed_start=model.proposed_start,
            proposed_end=model.proposed_end,
            location=model.location,
            attendees=json.loads(model.attendees_json) if model.attendees_json else [],
            status=status_map.get(model.status, SuggestionStatus.PENDING),
            calendar_event_id=model.calendar_event_id,
            has_conflict=model.has_conflict,
            conflict_details=model.conflict_details,
            alternative_slots=(
                json.loads(model.alternative_slots_json)
                if model.alternative_slots_json
                else []
            ),
            created_at=model.created_at,
            updated_at=model.updated_at,
            resolved_at=model.resolved_at,
        )

    @staticmethod
    def _extract_time(text: str) -> str:
        """Extract time from text using regex."""
        for pattern in _TIME_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _extract_date(text: str) -> str:
        """Extract date from text using regex."""
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _resolve_datetime(
        date_str: str,
        time_str: str,
        duration_minutes: int,
        reference_date: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        """Resolve date/time strings to actual datetimes."""
        if not date_str and not time_str:
            return None, None

        target_date = reference_date.date()

        # Resolve relative dates
        date_lower = date_str.lower().strip()
        if date_lower == "today":
            target_date = reference_date.date()
        elif date_lower == "tomorrow":
            target_date = (reference_date + timedelta(days=1)).date()
        elif date_lower == "day after tomorrow":
            target_date = (reference_date + timedelta(days=2)).date()
        elif date_lower in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ):
            day_names = [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]
            target_idx = day_names.index(date_lower)
            current_idx = reference_date.weekday()
            days_ahead = (target_idx - current_idx) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = (reference_date + timedelta(days=days_ahead)).date()
        elif date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        # Resolve time
        hour, minute = 10, 0  # Default: 10 AM
        if time_str:
            time_clean = time_str.strip().lower()
            try:
                # Try "HH:MM" or "H:MM AM/PM" formats
                for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
                    try:
                        parsed = datetime.strptime(time_clean, fmt)
                        hour, minute = parsed.hour, parsed.minute
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        start = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            minute,
            tzinfo=timezone.utc,
        )
        end = start + timedelta(minutes=duration_minutes)

        return start, end
