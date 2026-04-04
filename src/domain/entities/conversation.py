"""
Conversation entity — represents an agent conversation session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Message:
    """A single message in a conversation."""

    role: MessageRole
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    token_count: int = 0


@dataclass
class Conversation:
    """Agent conversation session with sliding window memory."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    messages: list[Message] = field(default_factory=list)
    summary: str | None = None  # Compressed summary of older messages
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    MAX_WINDOW_SIZE: int = 10  # Keep last N messages in active window

    def add_message(self, role: MessageRole, content: str, **kwargs: object) -> Message:
        """Add a message and maintain sliding window."""
        msg = Message(role=role, content=content, **kwargs)  # type: ignore[arg-type]
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc)
        return msg

    def get_active_window(self) -> list[Message]:
        """Return the most recent messages within the window."""
        return self.messages[-self.MAX_WINDOW_SIZE :]

    def get_total_tokens(self) -> int:
        """Total tokens consumed in this conversation."""
        return sum(m.token_count for m in self.messages)

    @property
    def message_count(self) -> int:
        return len(self.messages)
