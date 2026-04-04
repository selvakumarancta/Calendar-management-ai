"""
Gmail Email Adapter — reads emails via Gmail API for calendar intelligence.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from src.domain.entities.email_message import EmailMessage
from src.domain.interfaces.email_provider import EmailProviderPort

logger = logging.getLogger("calendar_agent.gmail_email")


class GmailEmailAdapter(EmailProviderPort):
    """Reads emails from Gmail using Google API with user's OAuth tokens."""

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._db_session_factory: Any = None

    def set_db_session_factory(self, factory: Any) -> None:
        """Inject DB session factory for token lookups."""
        self._db_session_factory = factory

    async def _get_service(self, user_id: uuid.UUID) -> Any:
        """Build an authorized Gmail API service for a user."""
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from src.infrastructure.security.token_encryption import decrypt_token

        tokens = await self._get_user_tokens(user_id)
        if not tokens:
            raise RuntimeError(f"No Gmail tokens found for user {user_id}")

        access_token = decrypt_token(tokens["access_token"])
        refresh_token = decrypt_token(tokens.get("refresh_token", ""))

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return build("gmail", "v1", credentials=credentials)

    async def _get_user_tokens(self, user_id: uuid.UUID) -> dict | None:
        """Look up Google OAuth tokens from provider_connections."""
        if not self._db_session_factory:
            return None

        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.user_id == user_id,
                    ProviderConnectionModel.provider == "google",
                    ProviderConnectionModel.status == "active",
                    ProviderConnectionModel.access_token != "dev-token",
                )
            )
            conn = result.scalars().first()
            if conn:
                return {
                    "access_token": conn.access_token,
                    "refresh_token": conn.refresh_token or "",
                }
        return None

    async def list_recent_emails(
        self,
        user_id: uuid.UUID,
        since: datetime,
        max_results: int = 50,
        query: str = "",
    ) -> list[EmailMessage]:
        """Fetch recent emails that may contain meeting/event/task info."""
        try:
            service = await self._get_service(user_id)

            # Build Gmail search query — focus on scheduling-related emails
            after_epoch = int(since.timestamp())
            search_parts = [f"after:{after_epoch}"]
            if query:
                search_parts.append(query)
            else:
                # Default: filter for scheduling-related keywords
                search_parts.append(
                    "("
                    "subject:(meeting OR schedule OR appointment OR invite OR "
                    "calendar OR call OR sync OR standup OR review OR deadline OR "
                    "task OR agenda OR conference OR webinar OR demo OR interview)"
                    " OR "
                    "from:calendar-notification@google.com"
                    " OR "
                    "from:noreply@google.com"
                    ")"
                )

            search_query = " ".join(search_parts)
            logger.info("Gmail search: %s (user=%s)", search_query, user_id)

            # List message IDs
            response = (
                service.users()
                .messages()
                .list(userId="me", q=search_query, maxResults=max_results)
                .execute()
            )
            messages = response.get("messages", [])
            if not messages:
                return []

            # Fetch full message details
            emails: list[EmailMessage] = []
            for msg_ref in messages[:max_results]:
                try:
                    full_msg = (
                        service.users()
                        .messages()
                        .get(userId="me", id=msg_ref["id"], format="full")
                        .execute()
                    )
                    email = self._parse_gmail_message(full_msg, user_id)
                    emails.append(email)
                except Exception as e:
                    logger.warning(
                        "Failed to parse Gmail message %s: %s", msg_ref["id"], e
                    )

            logger.info(
                "Fetched %d emails from Gmail for user %s", len(emails), user_id
            )
            return emails

        except Exception as e:
            logger.error("Gmail list_recent_emails failed for user %s: %s", user_id, e)
            return []

    async def get_email(
        self,
        user_id: uuid.UUID,
        message_id: str,
    ) -> EmailMessage | None:
        """Get a single email by Gmail message ID."""
        try:
            service = await self._get_service(user_id)
            full_msg = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            return self._parse_gmail_message(full_msg, user_id)
        except Exception as e:
            logger.error("Gmail get_email failed: %s", e)
            return None

    async def mark_processed(
        self,
        user_id: uuid.UUID,
        message_id: str,
    ) -> bool:
        """Add a 'CalendarAgent/Processed' label to mark the email."""
        try:
            service = await self._get_service(user_id)
            # Use STAR as a visible marker (creating custom labels needs extra API)
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": ["STARRED"]},
            ).execute()
            return True
        except Exception as e:
            logger.warning("Failed to mark message %s: %s", message_id, e)
            return False

    @staticmethod
    def _parse_gmail_message(msg: dict, user_id: uuid.UUID) -> EmailMessage:
        """Parse a Gmail API message into our domain EmailMessage."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

        # Parse sender
        sender_raw = headers.get("from", "")
        sender_name, sender_email = parseaddr(sender_raw)

        # Parse recipients
        to_raw = headers.get("to", "")
        recipients = [parseaddr(r.strip())[1] for r in to_raw.split(",") if r.strip()]

        cc_raw = headers.get("cc", "")
        cc = [parseaddr(r.strip())[1] for r in cc_raw.split(",") if r.strip()]

        # Parse date
        date_str = headers.get("date", "")
        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        # Extract body text
        body_text = GmailEmailAdapter._extract_body(msg.get("payload", {}))

        return EmailMessage(
            provider_message_id=msg.get("id", ""),
            provider="google",
            user_id=user_id,
            subject=headers.get("subject", "(no subject)"),
            sender_email=sender_email,
            sender_name=sender_name,
            recipients=recipients,
            cc=cc,
            body_text=body_text,
            body_snippet=msg.get("snippet", ""),
            received_at=received_at,
            thread_id=msg.get("threadId", ""),
            labels=msg.get("labelIds", []),
            has_attachments=bool(msg.get("payload", {}).get("parts")),
            is_read="UNREAD" not in msg.get("labelIds", []),
        )

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Extract plain text body from Gmail message payload."""
        # Simple text/plain
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        # Multipart — find text/plain part
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
            # Nested multipart
            if part.get("parts"):
                result = GmailEmailAdapter._extract_body(part)
                if result:
                    return result

        return ""
