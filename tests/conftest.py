"""Test configuration and shared fixtures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.domain.entities.calendar_event import CalendarEvent, EventStatus
from src.domain.entities.user import SubscriptionPlan, User


@pytest.fixture
def sample_user() -> User:
    """Create a sample user for testing."""
    return User(
        id=uuid.uuid4(),
        email="test@example.com",
        name="Test User",
        timezone="UTC",
        plan=SubscriptionPlan.PRO,
        is_active=True,
    )


@pytest.fixture
def sample_event() -> CalendarEvent:
    """Create a sample calendar event for testing."""
    return CalendarEvent(
        id=uuid.uuid4(),
        title="Team Standup",
        description="Daily standup meeting",
        location="Conference Room A",
        start_time=datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 30, 9, 30, tzinfo=timezone.utc),
        status=EventStatus.CONFIRMED,
    )


@pytest.fixture
def sample_event_afternoon() -> CalendarEvent:
    """Create a sample afternoon event for conflict testing."""
    return CalendarEvent(
        id=uuid.uuid4(),
        title="Sprint Planning",
        start_time=datetime(2026, 3, 30, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 30, 15, 0, tzinfo=timezone.utc),
        status=EventStatus.CONFIRMED,
    )
