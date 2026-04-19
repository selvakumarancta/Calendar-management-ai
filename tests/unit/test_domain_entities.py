"""
Unit tests for domain entities:  CalendarEvent  and  User .

Pure Python — no async, no DB, no imports from infrastructure.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.entities.calendar_event import (
    Attendee,
    CalendarEvent,
    EventStatus,
    Recurrence,
    RecurrenceFrequency,
    Reminder,
)
from src.domain.entities.user import SubscriptionPlan, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_NOW = datetime.now(_UTC)


def _ev(
    start_offset_h: int = 1,
    dur_h: int = 1,
    status: EventStatus = EventStatus.CONFIRMED,
) -> CalendarEvent:
    start = _NOW + timedelta(hours=start_offset_h)
    return CalendarEvent(
        id=uuid.uuid4(),
        title="Test Event",
        start_time=start,
        end_time=start + timedelta(hours=dur_h),
        status=status,
    )


def _user(plan: SubscriptionPlan = SubscriptionPlan.FREE) -> User:
    return User(
        id=uuid.uuid4(),
        email="alice@example.com",
        name="Alice",
        timezone="UTC",
        plan=plan,
        is_active=True,
    )


# ===========================================================================
# CalendarEvent
# ===========================================================================


class TestCalendarEventDurationMinutes:
    @pytest.mark.unit
    def test_one_hour_event(self):
        ev = _ev(dur_h=1)
        assert ev.duration_minutes == 60

    @pytest.mark.unit
    def test_half_hour_event(self):
        start = _NOW + timedelta(hours=1)
        ev = CalendarEvent(start_time=start, end_time=start + timedelta(minutes=30))
        assert ev.duration_minutes == 30

    @pytest.mark.unit
    def test_zero_duration_all_day(self):
        start = _NOW + timedelta(hours=1)
        ev = CalendarEvent(start_time=start, end_time=start, is_all_day=True)
        assert ev.duration_minutes == 0


class TestCalendarEventConflictsWith:
    @pytest.mark.unit
    def test_overlapping_events_conflict(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=3),
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=4),
        )
        assert ev1.conflicts_with(ev2)

    @pytest.mark.unit
    def test_adjacent_events_do_not_conflict(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=2),
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=3),
        )
        assert not ev1.conflicts_with(ev2)

    @pytest.mark.unit
    def test_non_overlapping_events_do_not_conflict(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=2),
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=5),
            end_time=_NOW + timedelta(hours=6),
        )
        assert not ev1.conflicts_with(ev2)

    @pytest.mark.unit
    def test_one_event_inside_another_conflicts(self):
        outer = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=5),
        )
        inner = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=3),
        )
        assert outer.conflicts_with(inner)

    @pytest.mark.unit
    def test_cancelled_event_never_conflicts(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=3),
            status=EventStatus.CANCELLED,
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=4),
        )
        assert not ev1.conflicts_with(ev2)

    @pytest.mark.unit
    def test_cancelled_other_event_never_conflicts(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=3),
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=4),
            status=EventStatus.CANCELLED,
        )
        assert not ev1.conflicts_with(ev2)

    @pytest.mark.unit
    def test_conflict_is_symmetric(self):
        ev1 = CalendarEvent(
            start_time=_NOW + timedelta(hours=1),
            end_time=_NOW + timedelta(hours=3),
        )
        ev2 = CalendarEvent(
            start_time=_NOW + timedelta(hours=2),
            end_time=_NOW + timedelta(hours=4),
        )
        assert ev1.conflicts_with(ev2) == ev2.conflicts_with(ev1)


class TestCalendarEventIsInPast:
    @pytest.mark.unit
    def test_past_event_returns_true(self):
        past = _NOW - timedelta(hours=2)
        ev = CalendarEvent(
            start_time=past - timedelta(hours=1),
            end_time=past,
        )
        assert ev.is_in_past()

    @pytest.mark.unit
    def test_future_event_returns_false(self):
        ev = _ev(start_offset_h=2, dur_h=1)
        assert not ev.is_in_past()

    @pytest.mark.unit
    def test_currently_running_event_not_in_past(self):
        ev = CalendarEvent(
            start_time=_NOW - timedelta(minutes=30),
            end_time=_NOW + timedelta(minutes=30),
        )
        assert not ev.is_in_past()


class TestCalendarEventAddAttendee:
    @pytest.mark.unit
    def test_add_new_attendee(self):
        ev = _ev()
        ev.add_attendee("bob@example.com", "Bob")
        assert any(a.email == "bob@example.com" for a in ev.attendees)

    @pytest.mark.unit
    def test_duplicate_attendee_not_added(self):
        ev = _ev()
        ev.add_attendee("bob@example.com")
        ev.add_attendee("bob@example.com")
        assert len([a for a in ev.attendees if a.email == "bob@example.com"]) == 1

    @pytest.mark.unit
    def test_add_attendee_sets_name(self):
        ev = _ev()
        ev.add_attendee("bob@example.com", "Bob Smith")
        bob = next(a for a in ev.attendees if a.email == "bob@example.com")
        assert bob.name == "Bob Smith"


class TestCalendarEventRemoveAttendee:
    @pytest.mark.unit
    def test_removes_existing_attendee(self):
        ev = _ev()
        ev.add_attendee("bob@example.com")
        ev.remove_attendee("bob@example.com")
        assert not any(a.email == "bob@example.com" for a in ev.attendees)

    @pytest.mark.unit
    def test_remove_non_existent_attendee_is_safe(self):
        ev = _ev()
        ev.remove_attendee("ghost@example.com")  # should not raise
        assert len(ev.attendees) == 0


class TestCalendarEventReschedule:
    @pytest.mark.unit
    def test_reschedule_updates_times(self):
        ev = _ev()
        new_start = _NOW + timedelta(hours=5)
        new_end = new_start + timedelta(hours=2)
        ev.reschedule(new_start, new_end)
        assert ev.start_time == new_start
        assert ev.end_time == new_end

    @pytest.mark.unit
    def test_reschedule_updates_updated_at(self):
        ev = _ev()
        old_updated = ev.updated_at
        new_start = _NOW + timedelta(hours=5)
        ev.reschedule(new_start, new_start + timedelta(hours=1))
        assert ev.updated_at >= old_updated


class TestCalendarEventCancel:
    @pytest.mark.unit
    def test_cancel_sets_status_to_cancelled(self):
        ev = _ev()
        assert ev.status == EventStatus.CONFIRMED
        ev.cancel()
        assert ev.status == EventStatus.CANCELLED

    @pytest.mark.unit
    def test_cancel_updates_updated_at(self):
        ev = _ev()
        old_updated = ev.updated_at
        ev.cancel()
        assert ev.updated_at >= old_updated


class TestCalendarEventDefaults:
    @pytest.mark.unit
    def test_default_status_is_confirmed(self):
        ev = CalendarEvent()
        assert ev.status == EventStatus.CONFIRMED

    @pytest.mark.unit
    def test_default_calendar_id_is_primary(self):
        ev = CalendarEvent()
        assert ev.calendar_id == "primary"

    @pytest.mark.unit
    def test_default_attendees_is_empty_list(self):
        ev = CalendarEvent()
        assert ev.attendees == []

    @pytest.mark.unit
    def test_each_event_has_unique_id(self):
        ev1 = CalendarEvent()
        ev2 = CalendarEvent()
        assert ev1.id != ev2.id


# ===========================================================================
# User entity
# ===========================================================================


class TestUserGetRequestLimit:
    @pytest.mark.unit
    def test_free_plan_limit_is_50(self):
        u = _user(SubscriptionPlan.FREE)
        assert u.get_request_limit() == 50

    @pytest.mark.unit
    def test_pro_plan_limit_is_500(self):
        u = _user(SubscriptionPlan.PRO)
        assert u.get_request_limit() == 500

    @pytest.mark.unit
    def test_business_plan_limit_is_2000(self):
        u = _user(SubscriptionPlan.BUSINESS)
        assert u.get_request_limit() == 2000

    @pytest.mark.unit
    def test_enterprise_plan_limit_is_large(self):
        u = _user(SubscriptionPlan.ENTERPRISE)
        assert u.get_request_limit() >= 10_000


class TestUserCanUsePrimaryModel:
    @pytest.mark.unit
    def test_free_cannot_use_primary_model(self):
        u = _user(SubscriptionPlan.FREE)
        assert not u.can_use_primary_model()

    @pytest.mark.unit
    def test_pro_can_use_primary_model(self):
        u = _user(SubscriptionPlan.PRO)
        assert u.can_use_primary_model()

    @pytest.mark.unit
    def test_business_can_use_primary_model(self):
        u = _user(SubscriptionPlan.BUSINESS)
        assert u.can_use_primary_model()

    @pytest.mark.unit
    def test_enterprise_can_use_primary_model(self):
        u = _user(SubscriptionPlan.ENTERPRISE)
        assert u.can_use_primary_model()


class TestUserHasValidGoogleToken:
    @pytest.mark.unit
    def test_no_token_returns_false(self):
        u = _user()
        assert not u.has_valid_google_token()

    @pytest.mark.unit
    def test_expired_token_returns_false(self):
        u = _user()
        u.google_access_token = "some-token"
        u.google_token_expiry = _NOW - timedelta(hours=1)
        assert not u.has_valid_google_token()

    @pytest.mark.unit
    def test_valid_future_token_returns_true(self):
        u = _user()
        u.google_access_token = "some-token"
        u.google_token_expiry = _NOW + timedelta(hours=1)
        assert u.has_valid_google_token()

    @pytest.mark.unit
    def test_token_without_expiry_returns_false(self):
        u = _user()
        u.google_access_token = "some-token"
        u.google_token_expiry = None
        assert not u.has_valid_google_token()


class TestUserUpdateGoogleTokens:
    @pytest.mark.unit
    def test_update_sets_access_token(self):
        u = _user()
        expiry = _NOW + timedelta(hours=1)
        u.update_google_tokens("new-access", "new-refresh", expiry)
        assert u.google_access_token == "new-access"

    @pytest.mark.unit
    def test_update_sets_refresh_token(self):
        u = _user()
        expiry = _NOW + timedelta(hours=1)
        u.update_google_tokens("new-access", "new-refresh", expiry)
        assert u.google_refresh_token == "new-refresh"

    @pytest.mark.unit
    def test_update_sets_expiry(self):
        u = _user()
        expiry = _NOW + timedelta(hours=1)
        u.update_google_tokens("new-access", "new-refresh", expiry)
        assert u.google_token_expiry == expiry

    @pytest.mark.unit
    def test_update_without_refresh_token_preserves_old(self):
        u = _user()
        u.google_refresh_token = "old-refresh"
        expiry = _NOW + timedelta(hours=1)
        u.update_google_tokens("new-access", None, expiry)
        assert u.google_refresh_token == "old-refresh"

    @pytest.mark.unit
    def test_update_sets_updated_at(self):
        u = _user()
        old_updated = u.updated_at
        expiry = _NOW + timedelta(hours=1)
        u.update_google_tokens("t", "r", expiry)
        assert u.updated_at >= old_updated


class TestSubscriptionPlanEnum:
    @pytest.mark.unit
    def test_all_plans_have_string_values(self):
        for plan in SubscriptionPlan:
            assert isinstance(plan.value, str)

    @pytest.mark.unit
    def test_plan_from_string(self):
        assert SubscriptionPlan("pro") == SubscriptionPlan.PRO
        assert SubscriptionPlan("free") == SubscriptionPlan.FREE


class TestEventStatusEnum:
    @pytest.mark.unit
    def test_all_statuses_have_lowercase_string_values(self):
        for status in EventStatus:
            assert status.value == status.value.lower()

    @pytest.mark.unit
    def test_status_from_string(self):
        assert EventStatus("confirmed") == EventStatus.CONFIRMED
        assert EventStatus("cancelled") == EventStatus.CANCELLED
