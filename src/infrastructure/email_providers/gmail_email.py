"""
Gmail Email Adapter — reads emails via Gmail API for calendar intelligence.
"""

from __future__ import annotations

import base64
import email.message
import logging
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from src.domain.entities.email_message import EmailMessage, ThreadMessage
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
        """Look up Google OAuth tokens — checks provider_connections first, then users table."""
        if not self._db_session_factory:
            return None

        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        async with self._db_session_factory() as session:
            # 1. Try provider_connections (org-level or personal-sentinel connections)
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
                    "provider_email": conn.provider_email or "",
                }

            # 2. Fall back to users table (main Google login stores tokens here)
            from src.infrastructure.persistence.models import UserModel

            result2 = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user = result2.scalars().first()
            if (
                user
                and user.google_access_token
                and user.google_access_token not in ("dev-token", "")
            ):
                return {
                    "access_token": user.google_access_token,
                    "refresh_token": user.google_refresh_token or "",
                    "provider_email": user.email or "",
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

            after_epoch = int(since.timestamp())
            search_parts = [f"after:{after_epoch}"]
            if query:
                search_parts.append(query)
            else:
                search_parts.append(
                    "("
                    "subject:(meeting OR schedule OR appointment OR invite OR "
                    "calendar OR call OR sync OR standup OR review OR deadline OR "
                    "task OR agenda OR conference OR webinar OR demo OR interview)"
                    " OR from:calendar-notification@google.com"
                    " OR from:noreply@google.com"
                    ")"
                )

            search_query = " ".join(search_parts)
            logger.info("Gmail search: %s (user=%s)", search_query, user_id)

            response = (
                service.users()
                .messages()
                .list(userId="me", q=search_query, maxResults=max_results)
                .execute()
            )
            messages = response.get("messages", [])
            if not messages:
                return []

            emails: list[EmailMessage] = []
            for msg_ref in messages[:max_results]:
                try:
                    full_msg = (
                        service.users()
                        .messages()
                        .get(userId="me", id=msg_ref["id"], format="full")
                        .execute()
                    )
                    parsed = self._parse_gmail_message(full_msg, user_id)
                    emails.append(parsed)
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

    async def get_thread_messages(
        self,
        user_id: uuid.UUID,
        thread_id: str,
        user_email: str = "",
    ) -> list[ThreadMessage]:
        """Fetch all messages in a Gmail thread for context.

        Returns messages sorted oldest-first so the agent can understand
        the full negotiation history (e.g. declined times, agreed times).
        """
        if not thread_id:
            return []
        try:
            service = await self._get_service(user_id)
            thread = (
                service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            messages = thread.get("messages", [])
            result: list[ThreadMessage] = []
            for msg in messages:
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                sender_raw = headers.get("from", "")
                sender_name, sender_addr = parseaddr(sender_raw)
                display_sender = sender_name or sender_addr

                date_str = headers.get("date", "")
                body = self._extract_body(msg.get("payload", {}))
                result.append(
                    ThreadMessage(
                        sender=display_sender,
                        recipient=headers.get("to", ""),
                        date=date_str,
                        body=body[:1500],  # Cap per-message length for token efficiency
                        is_from_user=(
                            bool(user_email)
                            and sender_addr.lower() == user_email.lower()
                        ),
                    )
                )
            return result  # oldest-first (Gmail returns oldest-first by default)
        except Exception as e:
            logger.warning(
                "Failed to fetch thread %s for user %s: %s", thread_id, user_id, e
            )
            return []

    async def create_draft_reply(
        self,
        user_id: uuid.UUID,
        thread_id: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        content_type: str = "plain",
    ) -> str:
        """Create a Gmail draft reply in the given thread.

        Returns the Gmail draft ID.
        """
        try:
            service = await self._get_service(user_id)

            # Build the MIME message
            msg = email.message.EmailMessage()
            msg["To"] = to
            msg["Subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
            if cc:
                msg["Cc"] = cc
            if content_type == "html":
                msg.set_content(body, subtype="html")
            else:
                msg.set_content(body)

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            draft_body: dict = {
                "message": {
                    "raw": raw,
                    "threadId": thread_id,
                }
            }
            draft = (
                service.users().drafts().create(userId="me", body=draft_body).execute()
            )
            draft_id: str = draft.get("id", "")
            logger.info(
                "Created Gmail draft %s in thread %s for user %s",
                draft_id,
                thread_id,
                user_id,
            )
            return draft_id
        except Exception as e:
            logger.error("Failed to create Gmail draft for user %s: %s", user_id, e)
            return ""

    async def send_draft(
        self,
        user_id: uuid.UUID,
        draft_provider_id: str,
    ) -> str:
        """Send an existing Gmail draft immediately (autopilot mode).

        Returns the sent message ID.
        """
        try:
            service = await self._get_service(user_id)
            result = (
                service.users()
                .drafts()
                .send(userId="me", body={"id": draft_provider_id})
                .execute()
            )
            message_id: str = result.get("id", "")
            logger.info(
                "Autopilot: sent draft %s → message %s for user %s",
                draft_provider_id,
                message_id,
                user_id,
            )
            return message_id
        except Exception as e:
            logger.error(
                "Failed to send draft %s for user %s: %s",
                draft_provider_id,
                user_id,
                e,
            )
            return ""

    async def mark_processed(
        self,
        user_id: uuid.UUID,
        message_id: str,
    ) -> bool:
        """Add a STARRED label to mark the email as processed by the agent."""
        try:
            service = await self._get_service(user_id)
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": ["STARRED"]},
            ).execute()
            return True
        except Exception as e:
            logger.warning("Failed to mark message %s: %s", message_id, e)
            return False

    async def setup_pubsub_watch(
        self,
        user_id: uuid.UUID,
        pubsub_topic: str,
    ) -> dict:
        """Register Gmail push notifications via Google Cloud Pub/Sub.

        Gmail will POST to the registered webhook URL whenever new messages
        arrive — eliminating the need for polling.

        Args:
            user_id: The user to watch.
            pubsub_topic: GCP Pub/Sub topic ARN, e.g.
                          'projects/my-project/topics/gmail-push'.
        Returns:
            dict with 'historyId' and 'expiration' (Unix ms timestamp).
        """
        try:
            service = await self._get_service(user_id)
            response = (
                service.users()
                .watch(
                    userId="me",
                    body={
                        "topicName": pubsub_topic,
                        "labelIds": ["INBOX"],
                        "labelFilterAction": "include",
                    },
                )
                .execute()
            )
            logger.info(
                "Gmail Pub/Sub watch registered for user %s (expires %s)",
                user_id,
                response.get("expiration"),
            )
            return {
                "history_id": response.get("historyId", ""),
                "expiration": response.get("expiration", ""),
            }
        except Exception as e:
            logger.error(
                "Failed to set up Gmail Pub/Sub watch for user %s: %s", user_id, e
            )
            return {}

    async def stop_pubsub_watch(
        self,
        user_id: uuid.UUID,
    ) -> bool:
        """Stop Gmail push notifications."""
        try:
            service = await self._get_service(user_id)
            service.users().stop(userId="me").execute()
            logger.info("Stopped Gmail Pub/Sub watch for user %s", user_id)
            return True
        except Exception as e:
            logger.warning(
                "Failed to stop Gmail Pub/Sub watch for user %s: %s", user_id, e
            )
            return False

    async def list_emails_since_history(
        self,
        user_id: uuid.UUID,
        history_id: str,
    ) -> list[EmailMessage]:
        """Fetch new messages since a Gmail history ID (used with Pub/Sub).

        When Gmail sends a push notification, it includes a historyId.
        This method fetches messages added since that checkpoint.
        """
        try:
            service = await self._get_service(user_id)
            history = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                )
                .execute()
            )
            new_emails: list[EmailMessage] = []
            for record in history.get("history", []):
                for msg_added in record.get("messagesAdded", []):
                    msg_id = msg_added["message"]["id"]
                    try:
                        full_msg = (
                            service.users()
                            .messages()
                            .get(userId="me", id=msg_id, format="full")
                            .execute()
                        )
                        new_emails.append(self._parse_gmail_message(full_msg, user_id))
                    except Exception as e:
                        logger.warning("Failed to fetch new message %s: %s", msg_id, e)
            return new_emails
        except Exception as e:
            logger.warning(
                "Failed to fetch history since %s for user %s: %s",
                history_id,
                user_id,
                e,
            )
            return []

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
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
            if part.get("parts"):
                result = GmailEmailAdapter._extract_body(part)
                if result:
                    return result

        return ""

