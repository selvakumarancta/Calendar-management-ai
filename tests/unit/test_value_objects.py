"""
Tests for Value Objects.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.value_objects import DateRange, TimeSlot, TokenUsage


class TestTimeSlot:
    @pytest.mark.unit
    def test_duration(self) -> None:
        slot = TimeSlot(
            start=datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc),
        )
        assert slot.duration_minutes == 60

    @pytest.mark.unit
    def test_overlaps(self) -> None:
        slot_a = TimeSlot(
            start=datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc),
        )
        slot_b = TimeSlot(
            start=datetime(2026, 3, 30, 9, 30, tzinfo=timezone.utc),
            end=datetime(2026, 3, 30, 10, 30, tzinfo=timezone.utc),
        )
        assert slot_a.overlaps(slot_b) is True

    @pytest.mark.unit
    def test_no_overlap(self) -> None:
        slot_a = TimeSlot(
            start=datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc),
        )
        slot_b = TimeSlot(
            start=datetime(2026, 3, 30, 11, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc),
        )
        assert slot_a.overlaps(slot_b) is False


class TestTokenUsage:
    @pytest.mark.unit
    def test_total_tokens(self) -> None:
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, model="gpt-4o-mini")
        assert usage.total_tokens == 150

    @pytest.mark.unit
    def test_cost_estimation_mini(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000, completion_tokens=1_000_000, model="gpt-4o-mini"
        )
        # mini: $0.15/1M input + $0.60/1M output
        assert abs(usage.estimated_cost_usd - 0.75) < 0.01

    @pytest.mark.unit
    def test_cost_estimation_4o(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000, completion_tokens=1_000_000, model="gpt-4o"
        )
        # 4o: $2.50/1M input + $10.00/1M output
        assert abs(usage.estimated_cost_usd - 12.50) < 0.01

    @pytest.mark.unit
    def test_cost_estimation_claude_sonnet(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            model="claude-sonnet-4-20250514",
        )
        # sonnet: $3.00/1M input + $15.00/1M output
        assert abs(usage.estimated_cost_usd - 18.00) < 0.01

    @pytest.mark.unit
    def test_cost_estimation_claude_haiku(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            model="claude-haiku-3-20250414",
        )
        # haiku: $0.25/1M input + $1.25/1M output
        assert abs(usage.estimated_cost_usd - 1.50) < 0.01

    @pytest.mark.unit
    def test_cost_estimation_unknown_model(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000, completion_tokens=1_000_000, model="unknown-model"
        )
        assert usage.estimated_cost_usd == 0.0


class TestDateRange:
    @pytest.mark.unit
    def test_valid_range(self) -> None:
        dr = DateRange(
            start=datetime(2026, 3, 30, tzinfo=timezone.utc),
            end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        )
        assert dr.start < dr.end

    @pytest.mark.unit
    def test_invalid_range_raises(self) -> None:
        with pytest.raises(ValueError, match="start must be before end"):
            DateRange(
                start=datetime(2026, 3, 31, tzinfo=timezone.utc),
                end=datetime(2026, 3, 30, tzinfo=timezone.utc),
            )
