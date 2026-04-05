"""
User Guides Service — generates and maintains AI-written preference guides for each user.

Two types of guide:
1. scheduling_preferences — When does the user prefer to meet? What patterns do they follow?
   e.g. "Prefers mornings (9-11am), avoids Fridays after 3pm, always buffers 15min"

2. email_style — What writing style does the user use in emails?
   e.g. "Casual, concise, starts emails with 'Hi [name]', signs off with 'Best'"

These guides are:
- Generated once during onboarding (analyzing past 60 days of emails + calendar)
- Updated incrementally as new patterns are observed
- Injected into the DraftComposerService system prompt for every draft

The guides make drafts sound like the user wrote them, not a robot.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.guides")

_SCHEDULING_PREFS_SYSTEM = """You are analyzing a user's email and calendar history to extract their scheduling preferences.

Look for patterns in:
- Preferred meeting times (morning/afternoon/evening preference)
- Days to avoid (e.g. no Friday afternoons, no early Monday meetings)
- Typical meeting duration (30min calls vs 60min working sessions)
- Buffer preferences (do they ever stack meetings or do they space them out?)
- Meeting frequency patterns
- Location preferences if mentioned (in-person vs remote)
- Timezone and working hours

Return a concise, prescriptive guide (3-8 bullet points) that can be injected directly into a scheduling agent prompt.
Write it in second person ("You prefer...", "You typically...").
Be specific with times, not vague. Focus on actionable patterns only.

Format: Plain text bullet points. No headers. Max 200 words."""

_EMAIL_STYLE_SYSTEM = """You are analyzing a user's sent emails to extract their writing style.

Look for patterns in:
- Opening phrases (How do they start? "Hi X," "Hello," "Hey X," etc.)
- Tone (formal, casual, friendly, direct)
- Sentence length (short vs elaborate)
- Closing phrases ("Best," "Thanks," "Cheers," etc.)
- Vocabulary patterns (do they use jargon, emojis, etc.)
- How they handle scheduling specifically (do they propose times directly or ask for availability?)

Return a concise style guide (3-6 bullet points) that a writing agent can use to match the user's voice.
Write it in second person ("You open with...", "You use...").
Focus on patterns that matter for scheduling emails.

Format: Plain text bullet points. No headers. Max 150 words."""

_SCHEDULING_PREFS_USER = """Analyze these calendar events and sent email snippets to extract scheduling preferences.

Recent calendar events (last {days} days):
{calendar_events}

Sent email snippets (scheduling-related):
{email_snippets}

Extract scheduling preferences as described."""

_EMAIL_STYLE_USER = """Analyze these sent email excerpts to extract the user's writing style.

Sent emails:
{email_samples}

Extract writing style patterns as described."""


class UserGuidesService:
    """
    Generates and stores AI-written user preference guides.
    Called during onboarding and periodically refreshed.
    """

    def __init__(
        self,
        llm_adapter: Any = None,
        db_session_factory: Any = None,
    ) -> None:
        self._llm = llm_adapter
        self._db = db_session_factory

    async def generate_all_guides(
        self,
        user_id: uuid.UUID,
        user_email: str,
        calendar_events: list[dict],
        sent_emails: list[dict],
    ) -> tuple[str, str]:
        """
        Generate both guides in parallel from history data.

        Args:
            user_id: The user to generate guides for.
            user_email: User's email address.
            calendar_events: List of past events [{title, start, end, day}].
            sent_emails: List of sent email snippets [{subject, body, date}].

        Returns:
            Tuple of (scheduling_preferences_guide, email_style_guide) as strings.
        """
        import asyncio

        scheduling_task = asyncio.create_task(
            self._generate_scheduling_prefs(calendar_events, sent_emails)
        )
        style_task = asyncio.create_task(
            self._generate_email_style(sent_emails, user_email)
        )

        scheduling_guide, style_guide = await asyncio.gather(
            scheduling_task, style_task
        )

        await self._save_guides(
            user_id=user_id,
            scheduling_guide=scheduling_guide,
            style_guide=style_guide,
            emails_analyzed=len(sent_emails),
        )

        logger.info(
            "Generated guides for user %s: scheduling=%d chars, style=%d chars",
            user_id,
            len(scheduling_guide),
            len(style_guide),
        )
        return scheduling_guide, style_guide

    async def _generate_scheduling_prefs(
        self,
        calendar_events: list[dict],
        sent_emails: list[dict],
    ) -> str:
        """Generate the scheduling preferences guide."""
        if not self._llm:
            return ""

        # Summarize calendar events (day/time patterns)
        event_lines = []
        for ev in calendar_events[:50]:
            day = ev.get("day", "")
            start = ev.get("start", "")
            end = ev.get("end", "")
            title = ev.get("title", "Meeting")
            event_lines.append(f"  {day} {start}-{end}: {title}")

        # Filter scheduling-related sent emails
        scheduling_keywords = re.compile(
            r"\b(meet|meeting|schedule|call|sync|available|free|time|slot)\b", re.I
        )
        import re as re_module

        relevant_emails = [
            e for e in sent_emails
            if scheduling_keywords.search(e.get("body", "") + e.get("subject", ""))
        ][:15]

        email_snippets = []
        for e in relevant_emails:
            snippet = e.get("body", "")[:300].replace("\n", " ")
            email_snippets.append(f"  [{e.get('date', '')}] Subject: {e.get('subject', '')}\n  {snippet}")

        user_content = _SCHEDULING_PREFS_USER.format(
            days=60,
            calendar_events="\n".join(event_lines) if event_lines else "No recent events.",
            email_snippets="\n\n".join(email_snippets) if email_snippets else "No sent emails found.",
        )

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _SCHEDULING_PREFS_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            return (response if isinstance(response, str) else response.get("content", "")).strip()
        except Exception as e:
            logger.warning("Failed to generate scheduling preferences guide: %s", e)
            return ""

    async def _generate_email_style(
        self,
        sent_emails: list[dict],
        user_email: str,
    ) -> str:
        """Generate the email style guide."""
        if not self._llm or not sent_emails:
            return ""

        import re as re_module
        scheduling_keywords = re_module.compile(
            r"\b(meet|meeting|schedule|call|sync|available|free|time|slot)\b",
            re_module.I,
        )

        # Sample scheduling-related sent emails
        samples = [
            e for e in sent_emails
            if scheduling_keywords.search(e.get("body", "") + e.get("subject", ""))
        ][:10]

        if not samples:
            samples = sent_emails[:8]  # Fall back to any sent emails

        email_samples_text = ""
        for i, e in enumerate(samples, 1):
            body = e.get("body", "")[:400].replace("\n", " ")
            email_samples_text += f"\nEmail {i} [{e.get('date', '')}]:\nSubject: {e.get('subject', '')}\nBody: {body}\n"

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _EMAIL_STYLE_SYSTEM},
                    {"role": "user", "content": _EMAIL_STYLE_USER.format(email_samples=email_samples_text)},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            return (response if isinstance(response, str) else response.get("content", "")).strip()
        except Exception as e:
            logger.warning("Failed to generate email style guide: %s", e)
            return ""

    async def get_user_guides(self, user_id: uuid.UUID) -> tuple[str, str]:
        """Load existing guides from DB for a user.

        Returns:
            Tuple of (scheduling_preferences_guide, email_style_guide).
            Returns empty strings if not yet generated.
        """
        if not self._db:
            return ("", "")
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import UserGuideModel

            async with self._db() as session:
                result = await session.execute(
                    select(UserGuideModel).where(
                        UserGuideModel.user_id == user_id
                    )
                )
                guides = result.scalars().all()
                scheduling = next(
                    (g.content for g in guides if g.guide_type == "scheduling_preferences"),
                    "",
                )
                style = next(
                    (g.content for g in guides if g.guide_type == "email_style"),
                    "",
                )
                return scheduling, style
        except Exception as e:
            logger.warning("Failed to load guides for user %s: %s", user_id, e)
            return ("", "")

    async def _save_guides(
        self,
        user_id: uuid.UUID,
        scheduling_guide: str,
        style_guide: str,
        emails_analyzed: int,
    ) -> None:
        """Persist both guides to the database, updating if they exist."""
        if not self._db:
            return
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import UserGuideModel

            async with self._db() as session:
                for guide_type, content in [
                    ("scheduling_preferences", scheduling_guide),
                    ("email_style", style_guide),
                ]:
                    if not content:
                        continue
                    result = await session.execute(
                        select(UserGuideModel).where(
                            UserGuideModel.user_id == user_id,
                            UserGuideModel.guide_type == guide_type,
                        )
                    )
                    existing = result.scalars().first()
                    if existing:
                        existing.content = content
                        existing.generated_at = datetime.now(timezone.utc)
                        existing.emails_analyzed = emails_analyzed
                    else:
                        session.add(
                            UserGuideModel(
                                user_id=user_id,
                                guide_type=guide_type,
                                content=content,
                                emails_analyzed=emails_analyzed,
                            )
                        )
                await session.commit()
        except Exception as e:
            logger.error("Failed to save user guides: %s", e)
