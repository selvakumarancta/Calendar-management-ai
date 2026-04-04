"""
Background Email Scanner — periodically scans all users' inboxes.
Runs as an asyncio background task within the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.email_scanner")


class EmailScannerWorker:
    """Background worker that periodically scans all active users' emails."""

    def __init__(
        self,
        container: Any,
        scan_interval_minutes: int = 15,
    ) -> None:
        self._container = container
        self._interval = scan_interval_minutes * 60  # Convert to seconds
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    async def start(self) -> None:
        """Start the background scanner."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("📧 Email scanner started (interval=%d min)", self._interval // 60)

    async def stop(self) -> None:
        """Stop the background scanner."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📧 Email scanner stopped")

    async def _run_loop(self) -> None:
        """Main loop — sleep then scan."""
        # Wait before first scan to let the app fully start
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._scan_all_users()
            except Exception as e:
                logger.error("Email scanner error: %s", e)

            await asyncio.sleep(self._interval)

    async def _scan_all_users(self) -> None:
        """Scan emails for all users with active provider connections."""
        from sqlalchemy import select

        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        db = self._container.database()

        async with db.session_factory() as session:
            # Find all active provider connections with email sync enabled
            # Skip dev-token connections — they don't have real API access
            result = await session.execute(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.status == "active",
                    ProviderConnectionModel.email_sync_enabled == True,  # noqa: E712
                    ProviderConnectionModel.access_token != "dev-token",
                )
            )
            connections = result.scalars().all()

        if not connections:
            logger.debug("No active email connections to scan")
            return

        logger.info("Scanning emails for %d active connections", len(connections))

        for conn in connections:
            try:
                await self._scan_user_provider(
                    user_id=conn.user_id,
                    org_id=conn.org_id,
                    provider=conn.provider,
                )
            except Exception as e:
                logger.warning(
                    "Email scan failed for user=%s provider=%s: %s",
                    conn.user_id,
                    conn.provider,
                    e,
                )

    async def _scan_user_provider(
        self,
        user_id: Any,
        org_id: Any,
        provider: str,
    ) -> None:
        """Scan a single user's provider for new emails."""
        from src.application.services.email_intelligence_service import (
            EmailIntelligenceService,
        )

        db = self._container.database()

        # Create the appropriate email adapter
        if provider == "google":
            from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

            settings = self._container.settings
            email_adapter = GmailEmailAdapter(
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
            )
            email_adapter.set_db_session_factory(db.session_factory)
        elif provider == "microsoft":
            from src.infrastructure.email_providers.outlook_email import (
                OutlookEmailAdapter,
            )

            email_adapter = OutlookEmailAdapter()
            email_adapter.set_db_session_factory(db.session_factory)
        else:
            logger.debug("Unsupported email provider: %s", provider)
            return

        # Create the intelligence service
        service = EmailIntelligenceService(
            llm_adapter=self._container.llm_adapter(),
            calendar_adapter=self._container.calendar_adapter(),
            db_session_factory=db.session_factory,
        )

        # Scan — look back 24 hours by default
        result = await service.scan_user_emails(
            user_id=user_id,
            email_provider=email_adapter,
            provider_name=provider,
            org_id=org_id,
            since_hours=24,
        )

        if result.suggestions_created > 0:
            logger.info(
                "User %s (%s): scanned=%d, actionable=%d, suggestions=%d",
                user_id,
                provider,
                result.emails_scanned,
                result.actionable_found,
                result.suggestions_created,
            )
