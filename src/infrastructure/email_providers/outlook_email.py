"""
Outlook Email Adapter — reads emails via Microsoft Graph API for calendar intelligence.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.domain.entities.email_message import EmailMessage
from src.domain.interfaces.email_provider import EmailProviderPort

logger = logging.getLogger("calendar_agent.outlook_email")


class OutlookEmailAdapter(EmailProviderPort):
    """Reads emails from Outlook/Microsoft 365 using Graph API."""

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self) -> None:
        self._db_session_factory: Any = None

    def set_db_session_factory(self, factory: Any) -> None:
        """Inject DB session factory for token lookups."""
        self._db_session_factory = factory

    async def _get_headers(self, user_id: uuid.UUID) -> dict[str, str]:
        """Get authorization headers for a user's Microsoft account."""
        from src.infrastructure.security.token_encryption import decrypt_token

        tokens = await self._get_user_tokens(user_id)
        if not tokens:
            raise RuntimeError(f"No Outlook tokens found for user {user_id}")

        access_token = decrypt_token(tokens["access_token"])
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def _get_user_tokens(self, user_id: uuid.UUID) -> dict | None:
        """Look up Microsoft OAuth tokens from provider_connections."""
        if not self._db_session_factory:
            return None

        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        async with self._db_session_factory() as session:
            result = await session.execute(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.user_id == user_id,
                    ProviderConnectionModel.provider == "microsoft",
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
            import httpx

            headers = await self._get_headers(user_id)

            # Use $filter and $search for scheduling-related emails
            since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            params: dict[str, str] = {
                "$top": str(max_results),
                "$orderby": "receivedDateTime desc",
                "$filter": f"receivedDateTime ge {since_iso}",
                "$select": "id,subject,from,toRecipients,ccRecipients,"
                "bodyPreview,body,receivedDateTime,hasAttachments,"
                "isRead,conversationId",
            }

            if query:
                params["$search"] = f'"{query}"'
            else:
                # Default: search for scheduling keywords
                params["$search"] = (
                    '"meeting" OR "schedule" OR "appointment" OR "invite" OR '
                    '"calendar" OR "call" OR "standup" OR "deadline" OR "task"'
                )

            url = f"{self.GRAPH_BASE}/me/messages"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

            emails: list[EmailMessage] = []
            for msg in data.get("value", []):
                emails.append(self._parse_graph_message(msg, user_id))

            logger.info(
                "Fetched %d emails from Outlook for user %s", len(emails), user_id
            )
            return emails

        except Exception as e:
            logger.error(
                "Outlook list_recent_emails failed for user %s: %s", user_id, e
            )
            return []

    async def get_email(
        self,
        user_id: uuid.UUID,
        message_id: str,
    ) -> EmailMessage | None:
        """Get a single email by Graph message ID."""
        try:
            import httpx

            headers = await self._get_headers(user_id)
            url = f"{self.GRAPH_BASE}/me/messages/{message_id}"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return self._parse_graph_message(response.json(), user_id)

        except Exception as e:
            logger.error("Outlook get_email failed: %s", e)
            return None

    async def get_thread_messages(
        self,
        user_id: uuid.UUID,
        thread_id: str,
        user_email: str = "",
    ) -> list:
        """Fetch all messages in an Outlook conversation thread."""
        from src.domain.entities.email_message import ThreadMessage

        try:
            import httpx

            headers = await self._get_headers(user_id)
            url = f"{self.GRAPH_BASE}/me/messages?$filter=conversationId eq '{thread_id}'&$orderby=receivedDateTime asc&$top=50"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    return []
                messages = response.json().get("value", [])

            result = []
            for msg in messages:
                from_data = msg.get("from", {}).get("emailAddress", {})
                sender_email = from_data.get("address", "")
                body_text = msg.get("body", {}).get("content", "")
                if msg.get("body", {}).get("contentType") == "html":
                    import re
                    body_text = re.sub(r"<[^>]+>", " ", body_text)
                    body_text = re.sub(r"\s+", " ", body_text).strip()
                received_at_str = msg.get("receivedDateTime", "")
                try:
                    received_at = datetime.fromisoformat(received_at_str.replace("Z", "+00:00"))
                except Exception:
                    received_at = datetime.now(timezone.utc)
                result.append(ThreadMessage(
                    message_id=msg.get("id", ""),
                    sender_email=sender_email,
                    body_text=body_text,
                    received_at=received_at,
                    is_from_user=(sender_email.lower() == user_email.lower()),
                ))
            return result
        except Exception as e:
            logger.error("Outlook get_thread_messages failed: %s", e)
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
        """Create a draft reply in Outlook Drafts folder via Microsoft Graph."""
        try:
            import httpx

            headers = await self._get_headers(user_id)
            headers["Content-Type"] = "application/json"

            payload: dict = {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if content_type == "html" else "Text",
                    "content": body,
                },
                "toRecipients": [{"emailAddress": {"address": to}}],
                "conversationId": thread_id,
            }
            if cc:
                payload["ccRecipients"] = [{"emailAddress": {"address": addr.strip()}} for addr in cc.split(",") if addr.strip()]

            url = f"{self.GRAPH_BASE}/me/messages"
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json().get("id", "")
        except Exception as e:
            logger.error("Outlook create_draft_reply failed: %s", e)
            return ""

    async def send_draft(
        self,
        user_id: uuid.UUID,
        draft_provider_id: str,
    ) -> str:
        """Send an existing Outlook draft immediately."""
        try:
            import httpx

            headers = await self._get_headers(user_id)
            url = f"{self.GRAPH_BASE}/me/messages/{draft_provider_id}/send"
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers)
                response.raise_for_status()
            return draft_provider_id
        except Exception as e:
            logger.error("Outlook send_draft failed: %s", e)
            return ""

    async def mark_processed(
        self,
        user_id: uuid.UUID,
        message_id: str,
    ) -> bool:
        """Flag the email as processed using categories."""
        try:
            import httpx

            headers = await self._get_headers(user_id)
            url = f"{self.GRAPH_BASE}/me/messages/{message_id}"

            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    url,
                    headers=headers,
                    json={"categories": ["CalendarAgent-Processed"]},
                )
                return response.status_code == 200

        except Exception as e:
            logger.warning("Failed to mark Outlook message %s: %s", message_id, e)
            return False

    @staticmethod
    def _parse_graph_message(msg: dict, user_id: uuid.UUID) -> EmailMessage:
        """Parse a Microsoft Graph API message into our domain EmailMessage."""
        from_data = msg.get("from", {}).get("emailAddress", {})
        sender_email = from_data.get("address", "")
        sender_name = from_data.get("name", "")

        recipients = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        cc = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("ccRecipients", [])
        ]

        # Parse received date
        date_str = msg.get("receivedDateTime", "")
        try:
            received_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            received_at = datetime.now(timezone.utc)

        # Body text
        body = msg.get("body", {})
        body_text = body.get("content", "")
        if body.get("contentType") == "html":
            # Strip HTML tags for plain text
            import re

            body_text = re.sub(r"<[^>]+>", " ", body_text)
            body_text = re.sub(r"\s+", " ", body_text).strip()

        return EmailMessage(
            provider_message_id=msg.get("id", ""),
            provider="microsoft",
            user_id=user_id,
            subject=msg.get("subject", "(no subject)"),
            sender_email=sender_email,
            sender_name=sender_name,
            recipients=recipients,
            cc=cc,
            body_text=body_text,
            body_snippet=msg.get("bodyPreview", ""),
            received_at=received_at,
            thread_id=msg.get("conversationId", ""),
            has_attachments=msg.get("hasAttachments", False),
            is_read=msg.get("isRead", False),
        )
