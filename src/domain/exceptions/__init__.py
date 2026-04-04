"""
Domain exceptions — typed business-rule violations.
These are caught and mapped to HTTP errors in the API layer.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base domain exception."""

    def __init__(self, message: str = "A domain error occurred") -> None:
        self.message = message
        super().__init__(self.message)


# --- Calendar Errors ---


class EventNotFoundError(DomainError):
    """Calendar event does not exist."""

    def __init__(self, event_id: str = "") -> None:
        super().__init__(f"Event not found: {event_id}")


class EventConflictError(DomainError):
    """Scheduling conflict detected."""

    def __init__(self, message: str = "Event conflicts with an existing event") -> None:
        super().__init__(message)


class EventInPastError(DomainError):
    """Attempt to create/modify an event in the past."""

    def __init__(self) -> None:
        super().__init__("Cannot create or modify events in the past")


class InvalidTimeRangeError(DomainError):
    """Start time is after end time."""

    def __init__(self) -> None:
        super().__init__("Event start time must be before end time")


# --- Auth Errors ---


class AuthenticationError(DomainError):
    """Invalid or expired credentials."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class TokenExpiredError(AuthenticationError):
    """OAuth or JWT token has expired."""

    def __init__(self) -> None:
        super().__init__("Token has expired")


class InsufficientPermissionsError(DomainError):
    """User lacks required permissions."""

    def __init__(self, action: str = "") -> None:
        super().__init__(f"Insufficient permissions for: {action}")


# --- Billing / Quota Errors ---


class QuotaExceededError(DomainError):
    """User has exceeded their plan's request limit."""

    def __init__(self, plan: str = "", limit: int = 0) -> None:
        super().__init__(
            f"Monthly request quota exceeded for {plan} plan (limit: {limit})"
        )


class InvalidPlanError(DomainError):
    """Invalid subscription plan specified."""

    def __init__(self, plan: str = "") -> None:
        super().__init__(f"Invalid plan: {plan}")


# --- Agent Errors ---


class AgentError(DomainError):
    """Error during agent execution."""

    def __init__(self, message: str = "Agent execution failed") -> None:
        super().__init__(message)


class AgentMaxIterationsError(AgentError):
    """Agent exceeded maximum iteration count."""

    def __init__(self, max_iterations: int = 0) -> None:
        super().__init__(f"Agent exceeded max iterations: {max_iterations}")


class CalendarProviderError(DomainError):
    """Error communicating with calendar provider (Google, Microsoft)."""

    def __init__(self, provider: str = "", message: str = "") -> None:
        super().__init__(f"{provider} Calendar API error: {message}")
