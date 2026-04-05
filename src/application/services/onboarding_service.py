"""
Onboarding Service — bootstraps a new user from their email and calendar history.

On first sign-up (or when manually triggered), this service:
1. Reads the last N days of Gmail for scheduling-related emails (sent + received)
2. Reads past calendar events to learn meeting patterns
3. Runs three agents in parallel:
   a. Backfill agent — adds past commitments to a dedicated "CalendarAgent" calendar
   b. Preferences agent — writes the scheduling_preferences guide
   c. Style agent — writes the email_style guide

After onboarding:
- The user's calendar is up-to-date with committed events
- Every future draft will use guides personalized to their actual patterns
- No manual configuration needed

This is the "cold start" solution — users start smart immediately.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("calendar_agent.onboarding")


class OnboardingStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OnboardingService:
    """
    Bootstraps a new user from their email and calendar history.
    Runs backfill + guide generation in parallel for fast onboarding.
    """

    def __init__(
        self,
        llm_adapter: Any = None,
        calendar_adapter: Any = None,
        db_session_factory: Any = None,
        lookback_days: int = 60,
    ) -> None:
        self._llm = llm_adapter
        self._calendar = calendar_adapter
        self._db = db_session_factory
        self._lookback_days = lookback_days

    async def run_onboarding(
        self,
        user_id: uuid.UUID,
        user_email: str,
        user_timezone: str,
        email_provider: Any,
        org_id: uuid.UUID | None = None,
    ) -> dict:
        """
        Full onboarding pipeline for a new user.

        Returns:
            Result dict with counts and status.
        """
        logger.info(
            "Starting onboarding for user %s (lookback=%d days)",
            user_id,
            self._lookback_days,
        )

        await self._save_onboarding_status(user_id, OnboardingStatus.IN_PROGRESS)

        result = {
            "user_id": str(user_id),
            "status": "in_progress",
            "calendar_events_backfilled": 0,
            "emails_analyzed": 0,
            "scheduling_guide_generated": False,
            "style_guide_generated": False,
            "errors": [],
        }

        try:
            # Run all three phases in parallel
            backfill_task = asyncio.create_task(
                self._backfill_calendar(
                    user_id, user_email, user_timezone, email_provider
                )
            )
            history_task = asyncio.create_task(
                self._gather_history(user_id, user_email, user_timezone, email_provider)
            )

            backfill_result, history = await asyncio.gather(
                backfill_task, history_task, return_exceptions=True
            )

            if isinstance(backfill_result, Exception):
                logger.warning("Backfill phase failed: %s", backfill_result)
                result["errors"].append(f"Backfill: {backfill_result}")
            else:
                result["calendar_events_backfilled"] = backfill_result.get(
                    "events_added", 0
                )

            if isinstance(history, Exception):
                logger.warning("History gathering failed: %s", history)
                result["errors"].append(f"History: {history}")
                history = {"calendar_events": [], "sent_emails": []}

            calendar_events: list[dict] = history.get("calendar_events", [])
            sent_emails: list[dict] = history.get("sent_emails", [])
            result["emails_analyzed"] = len(sent_emails)

            # Guide generation (uses gathered history)
            from src.application.services.user_guides_service import UserGuidesService

            guides_service = UserGuidesService(
                llm_adapter=self._llm,
                db_session_factory=self._db,
            )
            scheduling_guide, style_guide = await guides_service.generate_all_guides(
                user_id=user_id,
                user_email=user_email,
                calendar_events=calendar_events,
                sent_emails=sent_emails,
            )
            result["scheduling_guide_generated"] = bool(scheduling_guide)
            result["style_guide_generated"] = bool(style_guide)

            result["status"] = "completed"
            await self._save_onboarding_status(user_id, OnboardingStatus.COMPLETED)
            logger.info("Onboarding completed for user %s: %s", user_id, result)

        except Exception as e:
            logger.error("Onboarding failed for user %s: %s", user_id, e)
            result["status"] = "failed"
            result["errors"].append(str(e))
            await self._save_onboarding_status(user_id, OnboardingStatus.FAILED)

        return result

    async def _gather_history(
        self,
        user_id: uuid.UUID,
        user_email: str,
        user_timezone: str,
        email_provider: Any,
    ) -> dict:
        """Gather calendar history and sent email history in parallel."""
        since = datetime.now(timezone.utc) - timedelta(days=self._lookback_days)

        # Fetch past calendar events
        calendar_events: list[dict] = []
        if self._calendar:
            try:
                past_events = await self._calendar.list_events(
                    user_id,
                    since,
                    datetime.now(timezone.utc),
                )
                for ev in past_events:
                    calendar_events.append(
                        {
                            "title": ev.title,
                            "start": ev.start_time.strftime("%H:%M"),
                            "end": ev.end_time.strftime("%H:%M"),
                            "day": ev.start_time.strftime("%A"),
                            "date": ev.start_time.strftime("%Y-%m-%d"),
                        }
                    )
                logger.info(
                    "Gathered %d past calendar events for user %s",
                    len(calendar_events),
                    user_id,
                )
            except Exception as e:
                logger.warning("Could not fetch past calendar events: %s", e)

        # Fetch sent/received scheduling emails
        sent_emails: list[dict] = []
        if email_provider:
            try:
                emails = await email_provider.list_recent_emails(
                    user_id=user_id,
                    since=since,
                    max_results=100,
                    query="in:sent (meeting OR schedule OR call OR available)",
                )
                for email_obj in emails:
                    sent_emails.append(
                        {
                            "subject": email_obj.subject,
                            "body": email_obj.body_text[:800],
                            "date": email_obj.received_at.strftime("%Y-%m-%d"),
                            "sender": email_obj.sender_email,
                        }
                    )
            except Exception as e:
                logger.warning("Could not fetch email history: %s", e)

        return {
            "calendar_events": calendar_events,
            "sent_emails": sent_emails,
        }

    async def _backfill_calendar(
        self,
        user_id: uuid.UUID,
        user_email: str,
        user_timezone: str,
        email_provider: Any,
    ) -> dict:
        """
        Backfill events to the scheduling calendar from email history.

        Scans inbox for past confirmed meetings and adds them to the
        dedicated CalendarAgent calendar if they're not already there.
        """
        if not self._llm or not self._calendar or not email_provider:
            return {"events_added": 0}

        since = datetime.now(timezone.utc) - timedelta(days=self._lookback_days)
        events_added = 0

        try:
            # Fetch past emails mentioning confirmed meetings
            emails = await email_provider.list_recent_emails(
                user_id=user_id,
                since=since,
                max_results=50,
                query="(confirmed OR accepted OR agreed) (meeting OR call OR sync)",
            )

            for email_obj in emails:
                try:
                    # Quick LLM call to extract confirmed event details
                    event_data = await self._extract_confirmed_event(
                        email_obj, user_timezone
                    )
                    if not event_data:
                        continue

                    # Check if this event already exists in primary calendar
                    start = datetime.fromisoformat(event_data["start_iso"])
                    end = datetime.fromisoformat(event_data["end_iso"])
                    existing = await self._calendar.list_events(
                        user_id,
                        start - timedelta(minutes=30),
                        end + timedelta(minutes=30),
                    )
                    if existing:
                        continue  # Already on calendar

                    # Add to the scheduling calendar
                    from src.application.dto import CreateEventDTO

                    dto = CreateEventDTO(
                        title=event_data["summary"],
                        start_time=start,
                        end_time=end,
                        description=f"[Backfilled by CalendarAgent from email: {email_obj.subject}]",
                        attendee_emails=[email_obj.sender_email],
                    )
                    await self._calendar.create_event(user_id, dto)
                    events_added += 1
                    logger.debug(
                        "Backfilled event: %s at %s",
                        event_data["summary"],
                        event_data["start_iso"],
                    )

                except Exception as e:
                    logger.debug("Skipped backfill for '%s': %s", email_obj.subject, e)

        except Exception as e:
            logger.warning("Backfill phase error: %s", e)

        logger.info("Backfilled %d events for user %s", events_added, user_id)
        return {"events_added": events_added}

    async def _extract_confirmed_event(
        self, email_obj: Any, user_timezone: str
    ) -> dict | None:
        """Use LLM to extract confirmed meeting details from an email."""
        if not self._llm:
            return None

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prompt = f"""Does this email confirm a concrete meeting/call commitment with a specific date and time?
If yes, return JSON. If no, return null.

Subject: {email_obj.subject}
Date: {email_obj.received_at.strftime('%Y-%m-%d')}
Body (first 500 chars): {email_obj.body_text[:500]}

Today: {today}. Timezone: {user_timezone}.

If confirmed, respond with ONLY this JSON:
{{"summary": "Event title", "start_iso": "ISO8601 datetime with timezone offset", "end_iso": "ISO8601 datetime with timezone offset"}}
If not confirmed or no specific time, respond with: null"""

        try:
            response = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200,
            )
            text = (
                response if isinstance(response, str) else response.get("content", "")
            ).strip()
            if text.lower() in ("null", "none", ""):
                return None
            import json

            return json.loads(text)
        except Exception:
            return None

    async def get_onboarding_status(self, user_id: uuid.UUID) -> str:
        """Return the onboarding status for a user."""
        if not self._db:
            return OnboardingStatus.NOT_STARTED.value
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import (
                OnboardingStatusModel,
            )

            async with self._db() as session:
                result = await session.execute(
                    select(OnboardingStatusModel).where(
                        OnboardingStatusModel.user_id == user_id
                    )
                )
                record = result.scalars().first()
                return record.status if record else OnboardingStatus.NOT_STARTED.value
        except Exception:
            return OnboardingStatus.NOT_STARTED.value

    async def _save_onboarding_status(
        self, user_id: uuid.UUID, status: OnboardingStatus
    ) -> None:
        """Persist the onboarding status."""
        if not self._db:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import (
                OnboardingStatusModel,
            )

            async with self._db() as session:
                result = await session.execute(
                    select(OnboardingStatusModel).where(
                        OnboardingStatusModel.user_id == user_id
                    )
                )
                record = result.scalars().first()
                now = datetime.now(timezone.utc)
                if record:
                    record.status = status.value
                    record.updated_at = now
                else:
                    session.add(
                        OnboardingStatusModel(
                            user_id=user_id,
                            status=status.value,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await session.commit()
        except Exception as e:
            logger.warning("Could not save onboarding status: %s", e)
