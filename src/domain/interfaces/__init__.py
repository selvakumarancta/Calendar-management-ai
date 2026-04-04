"""
Domain interfaces (Ports) — abstract contracts for infrastructure adapters.
These define what the domain NEEDS, not HOW it's implemented.
Follows the Dependency Inversion Principle.
"""

from .cache import CachePort
from .calendar_provider import CalendarProviderPort
from .conversation_repository import ConversationRepositoryPort
from .event_repository import EventRepositoryPort
from .llm import LLMPort
from .usage_tracker import UsageTrackerPort
from .user_repository import UserRepositoryPort

__all__ = [
    "CalendarProviderPort",
    "CachePort",
    "LLMPort",
    "UserRepositoryPort",
    "EventRepositoryPort",
    "ConversationRepositoryPort",
    "UsageTrackerPort",
]
