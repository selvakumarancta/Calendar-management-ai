"""
WhatsApp Webhook Adapter — parses Meta Cloud API payloads and
extracts plain-text messages for downstream processing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("calendar_agent.whatsapp")


@dataclass
class WhatsAppMessage:
    """Normalized inbound WhatsApp message."""

    message_id: str
    from_phone: str  # e.g. "919876543210"
    display_phone: str  # formatted with country prefix
    text: str  # raw message body
    timestamp: int  # unix epoch
    phone_number_id: str  # our receiving phone number ID


class WhatsAppWebhookAdapter:
    """
    Parses Meta Cloud API webhook payloads.
    Handles text messages only (ignores media, reactions, statuses).
    """

    def __init__(
        self,
        verify_token: str,
        webhook_secret: str = "",
    ) -> None:
        self._verify_token = verify_token
        self._webhook_secret = webhook_secret

    # ------------------------------------------------------------------
    # Webhook verification (GET /webhooks/whatsapp)
    # ------------------------------------------------------------------

    def verify_challenge(
        self,
        hub_mode: str,
        hub_verify_token: str,
        hub_challenge: str,
    ) -> str | None:
        """
        Returns hub_challenge string if the request is valid, else None.
        Meta sends this GET request when you first register the webhook URL.
        """
        if hub_mode == "subscribe" and hub_verify_token == self._verify_token:
            return hub_challenge
        return None

    # ------------------------------------------------------------------
    # Payload HMAC verification
    # ------------------------------------------------------------------

    def verify_signature(self, raw_body: bytes, x_hub_signature_256: str) -> bool:
        """Verify Meta's HMAC-SHA256 signature header (optional but recommended)."""
        if not self._webhook_secret:
            return True  # skip if not configured
        expected = (
            "sha256="
            + hmac.new(
                self._webhook_secret.encode(),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, x_hub_signature_256 or "")

    # ------------------------------------------------------------------
    # Payload parsing
    # ------------------------------------------------------------------

    def parse_messages(self, payload: dict[str, Any]) -> list[WhatsAppMessage]:
        """
        Extract text messages from a Meta webhook payload.
        Returns an empty list for status updates, media, reactions, etc.
        """
        messages: list[WhatsAppMessage] = []

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

                for msg in value.get("messages", []):
                    # Only process plain text messages
                    if msg.get("type") != "text":
                        logger.debug(
                            "Skipping non-text WhatsApp message type=%s",
                            msg.get("type"),
                        )
                        continue

                    text_body = msg.get("text", {}).get("body", "").strip()
                    if not text_body:
                        continue

                    messages.append(
                        WhatsAppMessage(
                            message_id=msg.get("id", ""),
                            from_phone=msg.get("from", ""),
                            display_phone=msg.get("from", ""),
                            text=text_body,
                            timestamp=int(msg.get("timestamp", 0)),
                            phone_number_id=phone_number_id,
                        )
                    )
        return messages

    # ------------------------------------------------------------------
    # Reply sender (optional — sends back a text reply via Graph API)
    # ------------------------------------------------------------------

    async def send_reply(
        self,
        to_phone: str,
        message_text: str,
        access_token: str,
        phone_number_id: str,
    ) -> bool:
        """Send a text reply back to the sender via Meta Graph API."""
        try:
            import httpx

            url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": message_text},
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("WhatsApp reply failed: %s", exc)
            return False
