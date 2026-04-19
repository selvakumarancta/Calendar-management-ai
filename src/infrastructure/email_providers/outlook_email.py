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
        """Get authorization headers, refreshing the access token when it is near expiry."""
        access_token = await self._get_fresh_access_token(user_id)
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def _get_fresh_access_token(self, user_id: uuid.UUID) -> str:
        """
        Decrypt the stored MS access token and refresh it automatically when it
        expires within 5 minutes.  Refreshed tokens are persisted back to the DB.
        """
        from src.infrastructure.security.token_encryption import (
            decrypt_token,
            encrypt_token,
        )

        tokens = await self._get_user_tokens(user_id)
        if not tokens:
            raise RuntimeError(f"No Outlook tokens found for user {user_id}")

        access_token = decrypt_token(tokens["access_token"])
        if not access_token:
            raise RuntimeError(
                f"Could not decrypt Microsoft access token for user {user_id}"
            )

        expiry: datetime | None = tokens.get("expiry")
        enc_refresh: str = tokens.get("refresh_token", "")

        if expiry and enc_refresh:
            now = datetime.now(timezone.utc)
            expiry_aware = (
                expiry.replace(tzinfo=timezone.utc)
                if expiry.tzinfo is None
                else expiry
            )
            if (expiry_aware - now).total_seconds() < 300:  # within 5 min
                try:
                    refresh_token = decrypt_token(enc_refresh)
                    new_data = await self._ms_refresh(refresh_token)
                    access_token = new_data["access_token"]
                    await self._persist_refreshed_tokens(
                        user_id=user_id,
                        source=tokens.get("source", "provider"),
                        source_id=tokens.get("source_id", ""),
                        access_token=access_token,
                        refresh_token=new_data.get("refresh_token", refresh_token),
                        expires_in=int(new_data.get("expires_in", 3600)),
                    )
                except Exception as exc:
                    logger.warning(
                        "MS token refresh failed for user %s: %s — using existing token",
                        user_id,
                        exc,
                    )

        return access_token

    async def _ms_refresh(self, refresh_token: str) -> dict:
        """Call the Microsoft token endpoint to exchange a refresh token."""
        import os

        import httpx

        client_id = os.environ.get("MICROSOFT_CLIENT_ID", "")
        client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
        tenant_id = os.environ.get("MICROSOFT_TENANT_ID", "common")

        if not client_id or not refresh_token:
            raise RuntimeError("Cannot refresh MS token: missing client_id or refresh_token")

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "openid profile email offline_access Calendars.ReadWrite Mail.Read",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_in": data.get("expires_in", 3600),
        }

    async def _persist_refreshed_tokens(
        self,
        user_id: uuid.UUID,
        source: str,
        source_id: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> None:
        """Write a refreshed token pair back to whichever table issued the original."""
        from datetime import timedelta

        from sqlalchemy import select

        from src.infrastructure.security.token_encryption import encrypt_token

        if not self._db_session_factory:
            return

        enc_access = encrypt_token(access_token)
        enc_refresh = encrypt_token(refresh_token)
        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        async with self._db_session_factory() as session:
            if source == "provider":
                from src.infrastructure.persistence.org_models import (
                    ProviderConnectionModel,
                )

                try:
                    import uuid as _uuid_mod

                    conn_uuid = _uuid_mod.UUID(source_id)
                except (ValueError, AttributeError):
                    return

                result = await session.execute(
                    select(ProviderConnectionModel).where(
                        ProviderConnectionModel.id == conn_uuid
                    )
                )
                conn = result.scalar_one_or_none()
                if conn:
                    conn.access_token = enc_access
                    conn.refresh_token = enc_refresh
                    conn.token_expiry = new_expiry
                    await session.commit()
            else:  # source == "user"
                from src.infrastructure.persistence.models import UserModel

                result = await session.execute(
                    select(UserModel).where(UserModel.id == user_id)
                )
                model = result.scalar_one_or_none()
                if model:
                    model.microsoft_access_token = enc_access
                    model.microsoft_refresh_token = enc_refresh
                    model.microsoft_token_expiry = new_expiry
                    await session.commit()

        logger.info("MS tokens refreshed and persisted for user %s", user_id)

    async def _get_user_tokens(self, user_id: uuid.UUID) -> dict | None:
        """Look up Microsoft OAuth tokens — checks provider_connections first, then users table."""
        if not self._db_session_factory:
            return None

        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        async with self._db_session_factory() as session:
            # 1. Try provider_connections
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
                    "expiry": conn.token_expiry,
                    "source": "provider",
                    "source_id": str(conn.id),
                }

            # 2. Fall back to users table — microsoft_* columns set during MS OAuth login
            from src.infrastructure.persistence.models import UserModel

            result2 = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            user = result2.scalars().first()
            if (
                user
                and user.microsoft_access_token
                and user.microsoft_access_token not in ("dev-token", "")
            ):
                return {
                    "access_token": user.microsoft_access_token,
                    "refresh_token": user.microsoft_refresh_token or "",
                    "expiry": user.microsoft_token_expiry,
                    "source": "user",
                    "source_id": str(user.id),
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
