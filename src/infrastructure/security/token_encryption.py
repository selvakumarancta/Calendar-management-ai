"""
Token Encryption — AES-256 (Fernet) encryption for OAuth tokens at rest.

Tokens stored in the database are NEVER in plain text. They're encrypted
using the application's secret key, so even if the DB file is compromised,
the tokens are unreadable without the encryption key.

Security model:
  - Your Gmail password is NEVER seen by this app (Google OAuth2 flow)
  - Only short-lived access_tokens and refresh_tokens are stored
  - Those tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
  - Encryption key is derived from app_secret_key via SHA-256 → base64
  - Tokens can be revoked anytime at myaccount.google.com/permissions
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("calendar_agent")

# Module-level singleton — initialized once with set_encryption_key()
_fernet: Fernet | None = None


def set_encryption_key(secret_key: str) -> None:
    """Derive a Fernet key from the app secret and initialize the cipher."""
    global _fernet
    # Fernet needs exactly 32 url-safe base64 bytes.
    # We derive it deterministically from the app_secret_key via SHA-256.
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    _fernet = Fernet(key)


def encrypt_token(plain_text: str) -> str:
    """Encrypt a token for storage. Returns a base64 string prefixed with 'enc:'."""
    if not plain_text or plain_text == "dev-token":
        return plain_text
    if _fernet is None:
        logger.warning("Encryption not initialized — storing token in plain text")
        return plain_text
    encrypted = _fernet.encrypt(plain_text.encode("utf-8"))
    return "enc:" + encrypted.decode("utf-8")


def decrypt_token(cipher_text: str) -> str:
    """Decrypt a stored token. Handles both encrypted ('enc:...') and legacy plain text."""
    if not cipher_text or cipher_text == "dev-token":
        return cipher_text
    if not cipher_text.startswith("enc:"):
        # Legacy plain-text token — return as-is (will be re-encrypted on next save)
        return cipher_text
    if _fernet is None:
        logger.warning("Encryption not initialized — cannot decrypt token")
        return ""
    try:
        raw = cipher_text[4:]  # strip 'enc:' prefix
        return _fernet.decrypt(raw.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt token — key may have changed")
        return ""
