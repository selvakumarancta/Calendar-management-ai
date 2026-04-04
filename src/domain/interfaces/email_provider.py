"""Port: Email Provider — abstract interface for reading emails from any provider."""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from src.domain.entities.email_message import EmailMessage


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
        """Fetch recent emails since a given date.

        Args:
            user_id: The user whose mailbox to read.
            since: Only return emails received after this datetime.
            max_results: Maximum number of emails to return.
            query: Optional search query (provider-specific).
        """
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
    async def mark_processed(
        self,
        user_id: UUID,
        message_id: str,
    ) -> bool:
        """Mark an email as processed (e.g. add a label).
        Returns True on success."""
        ...
