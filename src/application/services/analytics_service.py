"""
Analytics Service — records and queries scheduling pipeline events.

Writes a row to scheduling_analytics for each significant action:
  - draft_composed
  - draft_sent (manual)
  - draft_sent_autopilot
  - draft_discarded
  - invite_verified
  - invite_skipped
  - link_created
  - link_booked
  - scan_completed
  - onboarding_completed

Provides query helpers for building the analytics dashboard.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.analytics")


class AnalyticsService:
    """Records and queries scheduling analytics events."""

    def __init__(self, db_session_factory: Any = None) -> None:
        self._db = db_session_factory

    # ------------------------------------------------------------------ #
    # Write helpers (call these from other services)
    # ------------------------------------------------------------------ #

    async def record(
        self,
        user_id: uuid.UUID,
        event_type: str,
        *,
        draft_id: uuid.UUID | None = None,
        thread_id: str | None = None,
        link_id: str | None = None,
        confidence: float | None = None,
        was_autopilot: bool = False,
        extra: dict | None = None,
    ) -> None:
        """
        Write a single analytics event row.

        This is fire-and-forget — errors are swallowed so they never block
        the main scheduling pipeline.
        """
        if not self._db:
            return
        try:
            from src.infrastructure.persistence.email_models import SchedulingAnalyticsModel

            async with self._db() as session:
                session.add(
                    SchedulingAnalyticsModel(
                        user_id=user_id,
                        event_type=event_type,
                        draft_id=draft_id,
                        thread_id=thread_id,
                        link_id=link_id,
                        confidence=confidence,
                        was_autopilot=was_autopilot,
                        extra_json=json.dumps(extra or {}),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.debug("Analytics write failed (non-fatal): %s", e)

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #

    async def get_summary(
        self,
        user_id: uuid.UUID,
        days: int = 30,
    ) -> dict:
        """
        Return aggregate counts for the dashboard.

        Returns a dict with event_type → count and some derived metrics.
        """
        if not self._db:
            return self._empty_summary()

        since = datetime.now(timezone.utc) - timedelta(days=days)
        try:
            from sqlalchemy import func, select

            from src.infrastructure.persistence.email_models import SchedulingAnalyticsModel

            async with self._db() as session:
                result = await session.execute(
                    select(
                        SchedulingAnalyticsModel.event_type,
                        func.count(SchedulingAnalyticsModel.id).label("count"),
                    )
                    .where(
                        SchedulingAnalyticsModel.user_id == user_id,
                        SchedulingAnalyticsModel.created_at >= since,
                    )
                    .group_by(SchedulingAnalyticsModel.event_type)
                )
                rows = result.all()

            counts: dict[str, int] = {row.event_type: row.count for row in rows}

            drafts_composed = counts.get("draft_composed", 0)
            drafts_sent = counts.get("draft_sent", 0) + counts.get("draft_sent_autopilot", 0)
            invites_verified = counts.get("invite_verified", 0)
            links_booked = counts.get("link_booked", 0)
            sales_filtered = counts.get("sales_email_filtered", 0)

            return {
                "period_days": days,
                "drafts_composed": drafts_composed,
                "drafts_sent": drafts_sent,
                "drafts_discarded": counts.get("draft_discarded", 0),
                "drafts_autopilot": counts.get("draft_sent_autopilot", 0),
                "invites_created": invites_verified,
                "invites_skipped": counts.get("invite_skipped", 0),
                "links_created": counts.get("link_created", 0),
                "links_booked": links_booked,
                "sales_emails_filtered": sales_filtered,
                "scans_completed": counts.get("scan_completed", 0),
                "onboardings_completed": counts.get("onboarding_completed", 0),
                "send_rate": round(drafts_sent / drafts_composed, 2) if drafts_composed else 0.0,
                "invite_success_rate": round(invites_verified / max(drafts_sent, 1), 2),
                "booking_rate": round(links_booked / max(counts.get("link_created", 1), 1), 2),
                "raw_counts": counts,
            }
        except Exception as e:
            logger.warning("Analytics query failed: %s", e)
            return self._empty_summary()

    async def get_recent_events(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        event_type: str | None = None,
    ) -> list[dict]:
        """Return individual analytics events for the activity feed."""
        if not self._db:
            return []
        try:
            from sqlalchemy import select

            from src.infrastructure.persistence.email_models import SchedulingAnalyticsModel

            async with self._db() as session:
                q = (
                    select(SchedulingAnalyticsModel)
                    .where(SchedulingAnalyticsModel.user_id == user_id)
                )
                if event_type:
                    q = q.where(SchedulingAnalyticsModel.event_type == event_type)
                q = q.order_by(SchedulingAnalyticsModel.created_at.desc()).limit(limit)
                result = await session.execute(q)
                rows = result.scalars().all()

            return [
                {
                    "id": str(row.id),
                    "event_type": row.event_type,
                    "draft_id": str(row.draft_id) if row.draft_id else None,
                    "thread_id": row.thread_id,
                    "link_id": row.link_id,
                    "confidence": row.confidence,
                    "was_autopilot": row.was_autopilot,
                    "extra": json.loads(row.extra_json or "{}"),
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("Analytics recent events query failed: %s", e)
            return []

    @staticmethod
    def _empty_summary() -> dict:
        return {
            "period_days": 30,
            "drafts_composed": 0,
            "drafts_sent": 0,
            "drafts_discarded": 0,
            "drafts_autopilot": 0,
            "invites_created": 0,
            "invites_skipped": 0,
            "links_created": 0,
            "links_booked": 0,
            "sales_emails_filtered": 0,
            "scans_completed": 0,
            "onboardings_completed": 0,
            "send_rate": 0.0,
            "invite_success_rate": 0.0,
            "booking_rate": 0.0,
            "raw_counts": {},
        }
