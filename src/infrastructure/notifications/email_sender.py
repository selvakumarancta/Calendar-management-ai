"""
Email Notification Sender — transactional email delivery.

Supports:
  - SMTP (aiosmtplib if available, smtplib fallback via executor)
  - Log-only when SMTP is not configured (dev / test mode)

Usage:
    from src.infrastructure.notifications.email_sender import send_invite_email
    await send_invite_email(settings, to_email, inviter_name, org_name, accept_url)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.settings import Settings

logger = logging.getLogger("calendar_agent.notifications")


async def send_invite_email(
    settings: "Settings",
    to_email: str,
    org_name: str,
    inviter_name: str,
    accept_url: str,
    role: str = "member",
) -> bool:
    """
    Send an org-invite email to the invited user.

    Returns True if the email was delivered (or logged in dev mode), False on error.

    Template:
        Subject: You've been invited to join <org_name> on Calendar Agent
        Body:    Plain-text + HTML invitation with accept link.
    """
    subject = f"You've been invited to join {org_name} on Calendar Agent"

    plain = (
        f"Hi,\n\n"
        f"{inviter_name} has invited you to join {org_name} on Calendar Agent "
        f"as a {role}.\n\n"
        f"Accept your invitation here:\n{accept_url}\n\n"
        f"This link expires in 7 days.\n\n"
        f"If you weren't expecting this invitation you can safely ignore this email.\n\n"
        f"— The Calendar Agent team"
    )

    html = (
        f"<p>Hi,</p>"
        f"<p><strong>{inviter_name}</strong> has invited you to join "
        f"<strong>{org_name}</strong> on Calendar Agent as a <em>{role}</em>.</p>"
        f'<p><a href="{accept_url}" style="'
        f"background:#7c3aed;color:#fff;padding:10px 20px;text-decoration:none;"
        f'border-radius:6px;display:inline-block">Accept Invitation</a></p>'
        f"<p>This link expires in 7 days.</p>"
        f"<p style='color:#6b7280;font-size:12px'>"
        f"If you weren't expecting this email, you can safely ignore it.</p>"
    )

    if not settings.smtp_host:
        # Dev / no-SMTP mode: just log the link so devs can test the flow
        logger.info(
            "📧 [INVITE EMAIL — dev mode] to=%s org=%s accept_url=%s",
            to_email,
            org_name,
            accept_url,
        )
        return True

    return await _send_smtp(settings, to_email, subject, plain, html)


async def _send_smtp(
    settings: "Settings",
    to_email: str,
    subject: str,
    plain: str,
    html: str,
) -> bool:
    """Send via SMTP.  Tries aiosmtplib first; falls back to smtplib in executor."""
    from_addr = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"

    try:
        import aiosmtplib

        message = _build_mime(from_addr, to_email, subject, plain, html)
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_use_tls,
        )
        logger.info("Invite email sent via aiosmtplib to %s", to_email)
        return True

    except ImportError:
        pass  # fall through to smtplib executor
    except Exception as exc:
        logger.error("aiosmtplib send failed: %s", exc)
        return False

    # Fallback: run smtplib in a thread executor so we don't block the event loop
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: _send_smtplib(settings, from_addr, to_email, subject, plain, html),
        )
        logger.info("Invite email sent via smtplib to %s", to_email)
        return True
    except Exception as exc:
        logger.error("smtplib send failed: %s", exc)
        return False


def _send_smtplib(
    settings: "Settings",
    from_addr: str,
    to_email: str,
    subject: str,
    plain: str,
    html: str,
) -> None:
    import smtplib

    msg = _build_mime(from_addr, to_email, subject, plain, html)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as srv:
        if settings.smtp_use_tls:
            srv.starttls()
        if settings.smtp_username:
            srv.login(settings.smtp_username, settings.smtp_password)
        srv.sendmail(settings.smtp_from_email, to_email, msg.as_string())


def _build_mime(
    from_addr: str, to_email: str, subject: str, plain: str, html: str
) -> object:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


async def send_password_reset_email(
    settings: "Settings",
    to_email: str,
    reset_url: str,
) -> bool:
    """
    Send a password-reset email to the user.

    Returns True on delivery (or log-only in dev), False on error.
    Token expires in 1 hour.
    """
    from_name = settings.smtp_from_name or "Calendar Agent"
    from_email = settings.smtp_from_email or "noreply@calendar-agent.local"
    from_addr = f"{from_name} <{from_email}>"

    subject = "Reset your Calendar Agent password"
    plain = (
        f"Hi,\n\n"
        f"We received a request to reset your Calendar Agent password.\n\n"
        f"Click the link below to choose a new password:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in 1 hour. If you didn't request this, you can safely ignore this email.\n\n"
        f"— The Calendar Agent Team"
    )
    html = (
        f"<p>Hi,</p>"
        f"<p>We received a request to reset your <strong>Calendar Agent</strong> password.</p>"
        f'<p><a href="{reset_url}">Reset my password</a></p>'
        f"<p>This link expires in <strong>1 hour</strong>. "
        f"If you didn't request this, you can safely ignore this email.</p>"
        f"<p>— The Calendar Agent Team</p>"
    )

    if not settings.smtp_host:
        logger.info(
            "Password reset email (no SMTP): to=%s reset_url=%s",
            to_email,
            reset_url,
        )
        return True

    return await _send_smtp(settings, to_email, subject, plain, html)
