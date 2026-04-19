"""Tests for src/infrastructure/notifications/email_sender.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_settings(smtp_host="smtp.example.com", **kwargs):
    s = MagicMock()
    s.smtp_host = smtp_host
    s.smtp_port = kwargs.get("smtp_port", 587)
    s.smtp_username = kwargs.get("smtp_username", "user")
    s.smtp_password = kwargs.get("smtp_password", "pass")
    s.smtp_use_tls = kwargs.get("smtp_use_tls", True)
    s.smtp_from_name = kwargs.get("smtp_from_name", "Calendar Agent")
    s.smtp_from_email = kwargs.get("smtp_from_email", "noreply@example.com")
    return s


# ---------------------------------------------------------------------------
# send_invite_email — dev mode (no smtp_host)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_dev_mode_returns_true():
    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings(smtp_host="")
    result = await send_invite_email(
        settings, "user@test.com", "Acme Corp", "Alice", "https://accept/token"
    )
    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_dev_mode_logs_link(caplog):
    import logging

    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings(smtp_host="")
    with caplog.at_level(logging.INFO, logger="calendar_agent.notifications"):
        await send_invite_email(
            settings, "u@x.com", "TestOrg", "Bob", "https://acc/tok"
        )
    assert "https://acc/tok" in caplog.text


# ---------------------------------------------------------------------------
# send_invite_email — SMTP via aiosmtplib
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_smtp_uses_aiosmtplib():
    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings()
    mock_aiosmtplib = MagicMock()
    mock_aiosmtplib.send = AsyncMock(return_value=None)

    with patch.dict("sys.modules", {"aiosmtplib": mock_aiosmtplib}):
        result = await send_invite_email(
            settings, "dest@example.com", "MyOrg", "Charlie", "https://link"
        )

    assert result is True
    mock_aiosmtplib.send.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_aiosmtplib_failure_returns_false():
    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings()
    mock_aiosmtplib = MagicMock()
    mock_aiosmtplib.send = AsyncMock(side_effect=Exception("SMTP error"))

    with patch.dict("sys.modules", {"aiosmtplib": mock_aiosmtplib}):
        result = await send_invite_email(
            settings, "dest@example.com", "MyOrg", "Dan", "https://link"
        )

    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_falls_back_to_smtplib_when_no_aiosmtplib():
    """When aiosmtplib is missing (ImportError), falls back to smtplib executor."""
    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings()

    import smtplib

    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch.dict("sys.modules", {"aiosmtplib": None}):
        with patch("smtplib.SMTP", return_value=mock_server):
            result = await send_invite_email(
                settings, "dest@example.com", "Org", "Eve", "https://link"
            )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_invite_email_smtplib_error_returns_false():
    """smtplib executor failure returns False."""
    from src.infrastructure.notifications.email_sender import send_invite_email

    settings = _make_settings()

    with patch.dict("sys.modules", {"aiosmtplib": None}):
        with patch("smtplib.SMTP", side_effect=Exception("connection refused")):
            result = await send_invite_email(
                settings, "dest@example.com", "Org", "Frank", "https://link"
            )

    assert result is False


# ---------------------------------------------------------------------------
# send_password_reset_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_password_reset_email_dev_mode_returns_true():
    from src.infrastructure.notifications.email_sender import send_password_reset_email

    settings = _make_settings(smtp_host="")
    settings.smtp_from_name = "Calendar Agent"
    settings.smtp_from_email = "noreply@x.com"
    result = await send_password_reset_email(
        settings, "user@test.com", "https://reset/token"
    )
    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_password_reset_email_smtp_succeeds():
    from src.infrastructure.notifications.email_sender import send_password_reset_email

    settings = _make_settings()
    mock_aiosmtplib = MagicMock()
    mock_aiosmtplib.send = AsyncMock(return_value=None)

    with patch.dict("sys.modules", {"aiosmtplib": mock_aiosmtplib}):
        result = await send_password_reset_email(
            settings, "u@x.com", "https://reset/tok"
        )

    assert result is True


# ---------------------------------------------------------------------------
# _build_mime
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_mime_creates_multipart_message():
    from src.infrastructure.notifications.email_sender import _build_mime

    msg = _build_mime(
        "Sender <s@e.com>", "recv@e.com", "Subject", "plain text", "<p>html</p>"
    )
    assert msg["Subject"] == "Subject"
    assert msg["From"] == "Sender <s@e.com>"
    assert msg["To"] == "recv@e.com"


# ---------------------------------------------------------------------------
# _send_smtplib
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_send_smtplib_uses_starttls_when_configured():
    from src.infrastructure.notifications.email_sender import _send_smtplib

    settings = _make_settings(smtp_use_tls=True, smtp_username="u", smtp_password="p")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_server):
        _send_smtplib(
            settings, "Sender <s@e.com>", "recv@e.com", "Subj", "plain", "html"
        )

    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("u", "p")


@pytest.mark.unit
def test_send_smtplib_skips_login_when_no_username():
    from src.infrastructure.notifications.email_sender import _send_smtplib

    settings = _make_settings(smtp_use_tls=False, smtp_username="", smtp_password="")
    mock_server = MagicMock()
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_server):
        _send_smtplib(
            settings, "Sender <s@e.com>", "recv@e.com", "Subj", "plain", "html"
        )

    mock_server.starttls.assert_not_called()
    mock_server.login.assert_not_called()
