"""
Value objects — immutable domain concepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class TimeSlot:
    """An available time slot (immutable value object)."""

    start: datetime
    end: datetime

    @property
    def duration_minutes(self) -> int:
        delta = self.end - self.start
        return int(delta.total_seconds() / 60)

    def overlaps(self, other: TimeSlot) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt <= self.end

    def __str__(self) -> str:
        fmt = "%Y-%m-%d %H:%M"
        return f"{self.start.strftime(fmt)} — {self.end.strftime(fmt)}"


@dataclass(frozen=True)
class WorkingHours:
    """User's working hours preference."""

    start: time
    end: time
    days: tuple[int, ...] = (0, 1, 2, 3, 4)  # Mon-Fri

    def is_within(self, dt: datetime) -> bool:
        """Check if a datetime falls within working hours."""
        if dt.weekday() not in self.days:
            return False
        return self.start <= dt.time() <= self.end


@dataclass(frozen=True)
class DateRange:
    """A date range for querying events."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("DateRange start must be before end")


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption for a single LLM call."""

    prompt_tokens: int
    completion_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost estimation based on model."""
        # (input_rate_per_token, output_rate_per_token)
        rates: dict[str, tuple[float, float]] = {
            # OpenAI
            "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
            "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
            # Anthropic
            "claude-sonnet-4-20250514": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-haiku-3-20250414": (0.25 / 1_000_000, 1.25 / 1_000_000),
            # Aliases for model families
            "claude-3-5-sonnet": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-3-5-haiku": (0.80 / 1_000_000, 4.00 / 1_000_000),
        }
        input_rate, output_rate = rates.get(self.model, (0.0, 0.0))
        return (self.prompt_tokens * input_rate) + (
            self.completion_tokens * output_rate
        )
