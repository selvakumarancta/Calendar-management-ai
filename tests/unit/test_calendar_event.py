"""
Tests for CalendarEvent entity.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.entities.calendar_event import CalendarEvent, EventStatus


class TestCalendarEvent:
    """Test CalendarEvent domain entity."""

    @pytest.mark.unit
    def test_duration_minutes(self, sample_event: CalendarEvent) -> None:
        assert sample_event.duration_minutes == 30

    @pytest.mark.unit
    def test_conflicts_with_overlapping(self, sample_event: CalendarEvent) -> None:
        overlapping = CalendarEvent(
            title="Overlapping",
            start_time=datetime(2026, 3, 30, 9, 15, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 30, 9, 45, tzinfo=timezone.utc),
        )
        assert sample_event.conflicts_with(overlapping) is True

    @pytest.mark.unit
    def test_no_conflict_with_non_overlapping(
        self, sample_event: CalendarEvent
    ) -> None:
        non_overlapping = CalendarEvent(
            title="Later",
            start_time=datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 30, 11, 0, tzinfo=timezone.utc),
        )
        assert sample_event.conflicts_with(non_overlapping) is False

    @pytest.mark.unit
    def test_cancelled_event_no_conflict(self, sample_event: CalendarEvent) -> None:
        cancelled = CalendarEvent(
            title="Cancelled",
            start_time=datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 3, 30, 9, 30, tzinfo=timezone.utc),
            status=EventStatus.CANCELLED,
        )
        assert sample_event.conflicts_with(cancelled) is False

    @pytest.mark.unit
    def test_add_attendee(self, sample_event: CalendarEvent) -> None:
        sample_event.add_attendee("john@example.com", "John")
        assert len(sample_event.attendees) == 1
        assert sample_event.attendees[0].email == "john@example.com"

    @pytest.mark.unit
    def test_add_duplicate_attendee(self, sample_event: CalendarEvent) -> None:
        sample_event.add_attendee("john@example.com")
        sample_event.add_attendee("john@example.com")
        assert len(sample_event.attendees) == 1

    @pytest.mark.unit
    def test_remove_attendee(self, sample_event: CalendarEvent) -> None:
        sample_event.add_attendee("john@example.com")
        sample_event.remove_attendee("john@example.com")
        assert len(sample_event.attendees) == 0

    @pytest.mark.unit
    def test_reschedule(self, sample_event: CalendarEvent) -> None:
        new_start = datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc)
        new_end = datetime(2026, 3, 31, 10, 30, tzinfo=timezone.utc)
        sample_event.reschedule(new_start, new_end)
        assert sample_event.start_time == new_start
        assert sample_event.end_time == new_end

    @pytest.mark.unit
    def test_cancel(self, sample_event: CalendarEvent) -> None:
        sample_event.cancel()
        assert sample_event.status == EventStatus.CANCELLED

    @pytest.mark.unit
    def test_to_summary_string(self, sample_event: CalendarEvent) -> None:
        summary = sample_event.to_summary_string()
        assert "Team Standup" in summary
        assert "09:00" in summary
