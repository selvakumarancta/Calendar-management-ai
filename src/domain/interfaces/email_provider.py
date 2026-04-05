"""Port: Email Provider — abstract interface for reading emails from any provider."""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from src.domain.entities.email_message import EmailMessage, ThreadMessage


class EmailProviderPort(abc.ABC):
    """Abstract email provider (Gmail, Outlook, etc.)."""

    @abc.abstractmethod
    async def list_recent_emails(
        self,
        user_id: UUID,
        since: datetime,
        max_results: int = 50,
        query: str = "",
    ) -> list[EmailMessage]:
        """Fetch recent emails since a given date."""
        ...

    @abc.abstractmethod
    async def get_email(
        self,
        user_id: UUID,
        message_id: str,
    ) -> EmailMessage | None:
        """Get a single email by its provider message ID."""
        ...

    @abc.abstractmethod
    async def get_thread_messages(
        self,
        user_id: UUID,
        thread_id: str,
        user_email: str = "",
    ) -> list[ThreadMessage]:
        """Fetch all messages in a thread for context.

        Args:
            user_id: The user whose mailbox to read.
            thread_id: The provider thread/conversation ID.
            user_email: The user's own email (to mark is_from_user).
        """
        ...

    @abc.abstractmethod
    async def create_draft_reply(
        self,
        user_id: UUID,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        content_type: str = "plain",
    ) -> str:
        """Create a draft reply in the user's Gmail drafts folder.

        Returns:
            The provider draft ID (for later reference/deletion).
        """
        ...

    @abc.abstractmethod
    async def send_draft(
        self,
        user_id: UUID,
        draft_provider_id: str,
    ) -> str:
        """Send an existing draft immediately (autopilot mode).

        Returns:
            The sent message ID.
        """
        ...

    @abc.abstractmethod
    async def mark_processed(
        self,
        user_id: UUID,
        message_id: str,
    ) -> bool:
        """Mark an email as processed (e.g. add a label).
        Returns True on success."""
        ...

    async def setup_pubsub_watch(
        self,
        user_id: UUID,
        pubsub_topic: str,
    ) -> dict:
        """Set up Gmail push notifications via Pub/Sub.

        Returns metadata including expiration and history ID.
        Default implementation returns empty dict (optional capability).
        """
        return {}

    async def stop_pubsub_watch(
        self,
        user_id: UUID,
    ) -> bool:
        """Stop Gmail push notifications.
        Default implementation is a no-op."""
        return True
