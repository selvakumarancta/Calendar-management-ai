"""
Data Transfer Objects — validated structures for crossing layer boundaries.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

# --- Calendar DTOs ---


class CreateEventDTO(BaseModel):
    """Input for creating a calendar event."""

    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    location: str | None = None
    start_time: datetime
    end_time: datetime
    is_all_day: bool = False
    calendar_id: str = "primary"
    attendee_emails: list[str] = Field(default_factory=list)
    reminder_minutes: int = 15


class UpdateEventDTO(BaseModel):
    """Input for updating a calendar event."""

    event_id: str
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    attendee_emails: list[str] | None = None


class EventResponseDTO(BaseModel):
    """Output representation of a calendar event."""

    id: str
    provider_event_id: str | None = None
    title: str
    description: str | None = None
    location: str | None = None
    start_time: datetime
    end_time: datetime
    is_all_day: bool = False
    status: str = "confirmed"
    attendees: list[str] = Field(default_factory=list)
    duration_minutes: int = 0


class FreeSlotDTO(BaseModel):
    """An available time slot."""

    start: datetime
    end: datetime
    duration_minutes: int


class DateRangeDTO(BaseModel):
    """Input for querying a date range."""

    start: datetime
    end: datetime
    calendar_id: str = "primary"


# --- Chat DTOs ---


class ChatRequestDTO(BaseModel):
    """Input for a chat message to the agent."""

    message: str = Field(..., min_length=1, max_length=5000)
    conversation_id: UUID | None = None


class ChatResponseDTO(BaseModel):
    """Output from the agent."""

    message: str
    conversation_id: UUID
    events_affected: list[EventResponseDTO] = Field(default_factory=list)
    token_usage: TokenUsageDTO | None = None


class TokenUsageDTO(BaseModel):
    """Token consumption details."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    estimated_cost_usd: float = 0.0


# --- Auth DTOs ---


class LoginResponseDTO(BaseModel):
    """JWT login response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


class UserProfileDTO(BaseModel):
    """User profile output."""

    id: UUID
    email: str
    name: str
    timezone: str
    plan: str
    monthly_requests_used: int = 0
    monthly_request_limit: int = 0


# --- Agent Routing ---


class RequestComplexity(str, Enum):
    """Agent request complexity classification."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    DETERMINISTIC = "deterministic"  # No LLM needed
