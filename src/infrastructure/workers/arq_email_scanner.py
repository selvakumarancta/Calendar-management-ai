"""
Durable Email Scanner — ARQ worker implementation.

Why ARQ instead of bare asyncio.Task:
  - Jobs survive app restarts (stored in Redis)
  - Redis distributed lock prevents two app instances from double-scanning
  - Automatic retry on failure (configurable via ARQ task settings)
  - Job deduplication via cron + unique keys
  - Visibility: inspect pending/failed jobs with arq CLI

Usage:
  Start worker:   arq src.infrastructure.workers.arq_email_scanner.WorkerSettings
  The FastAPI app schedules cron via lifespan (see app.py).

If ARQ / Redis is unavailable the old in-process asyncio fallback is used
automatically (zero-config for development).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.email_scanner")

# ---------------------------------------------------------------------------
# ARQ task — the unit of work executed by the worker process
# ---------------------------------------------------------------------------


async def scan_all_users_job(ctx: dict, since_hours: int = 72) -> dict:
    """
    ARQ task: scan all active email connections.
    ``ctx`` is injected by ARQ and contains the application container.
    """
    container: Any = ctx.get("container")
    if container is None:
        logger.error("ARQ context missing container — skipping scan")
        return {"skipped": True}

    from sqlalchemy import select

    from src.infrastructure.persistence.org_models import ProviderConnectionModel

    db = container.database()

    async with db.session_factory() as session:
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
        return {"connections": 0}

    logger.info("Scanning %d connections (window=%dh)", len(connections), since_hours)

    successes, failures = 0, 0
    for conn in connections:
        try:
            await _scan_single_connection(
                container=container,
                user_id=conn.user_id,
                org_id=conn.org_id,
                provider=conn.provider,
                since_hours=since_hours,
            )
            successes += 1
        except Exception as exc:
            logger.warning(
                "Scan failed user=%s provider=%s: %s", conn.user_id, conn.provider, exc
            )
            failures += 1

    return {"scanned": successes, "failed": failures}


async def _scan_single_connection(
    container: Any,
    user_id: Any,
    org_id: Any,
    provider: str,
    since_hours: int,
) -> None:
    """Scan one provider connection's inbox."""
    from src.application.services.email_intelligence_service import (
        EmailIntelligenceService,
    )

    db = container.database()

    if provider == "google":
        from src.infrastructure.email_providers.gmail_email import GmailEmailAdapter

        settings = container.settings
        adapter = GmailEmailAdapter(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        adapter.set_db_session_factory(db.session_factory)
    elif provider == "microsoft":
        from src.infrastructure.email_providers.outlook_email import OutlookEmailAdapter

        adapter = OutlookEmailAdapter()
        adapter.set_db_session_factory(db.session_factory)
    else:
        logger.debug("Unsupported provider: %s", provider)
        return

    svc = EmailIntelligenceService(
        llm_adapter=container.llm_adapter(),
        calendar_adapter=container.calendar_adapter(),
        db_session_factory=db.session_factory,
    )
    result = await svc.scan_user_emails(
        user_id=user_id,
        email_provider=adapter,
        provider_name=provider,
        org_id=org_id,
        since_hours=since_hours,
    )
    if result.suggestions_created > 0:
        logger.info(
            "user=%s provider=%s scanned=%d suggestions=%d",
            user_id,
            provider,
            result.emails_scanned,
            result.suggestions_created,
        )


# ---------------------------------------------------------------------------
# ARQ WorkerSettings — used by `arq src.infrastructure.workers.arq_email_scanner.WorkerSettings`
# ---------------------------------------------------------------------------


async def cleanup_stale_records_job(ctx: dict) -> dict:
    """
    ARQ cron task: prune stale database rows daily.

    - ``stripe_processed_events`` rows older than 30 days (safe to discard;
      Stripe won't re-send events that far back).
    - ``OrgPendingInviteModel`` rows that are expired AND unaccepted older than
      90 days (GDPR minimisation — keeping them forever is unnecessary).
    """
    container: Any = ctx.get("container")
    if container is None:
        logger.error("ARQ context missing container — skipping cleanup")
        return {"skipped": True}

    from datetime import timedelta

    from sqlalchemy import delete as sql_delete

    from src.infrastructure.persistence.models import (
        OrgPendingInviteModel,
        StripeProcessedEventModel,
    )

    now = datetime.now(timezone.utc)

    db = container.database()
    stripe_deleted = invite_deleted = 0

    async with db.session_factory() as session:
        # 1. Stripe events older than 30 days
        cutoff_stripe = now - timedelta(days=30)
        res = await session.execute(
            sql_delete(StripeProcessedEventModel).where(
                StripeProcessedEventModel.processed_at < cutoff_stripe
            )
        )
        stripe_deleted = res.rowcount

        # 2. Expired, unaccepted pending invites older than 90 days
        cutoff_invite = now - timedelta(days=90)
        res = await session.execute(
            sql_delete(OrgPendingInviteModel).where(
                OrgPendingInviteModel.accepted == False,  # noqa: E712
                OrgPendingInviteModel.expires_at < cutoff_invite,
            )
        )
        invite_deleted = res.rowcount

        await session.commit()

    logger.info(
        "cleanup: deleted %d stripe events, %d expired invites",
        stripe_deleted,
        invite_deleted,
    )
    return {"stripe_events_deleted": stripe_deleted, "invites_deleted": invite_deleted}


class WorkerSettings:
    """
    ARQ worker configuration.

    The worker process must also have access to the DI container.
    We build it lazily in ``on_startup``.
    """

    functions = [scan_all_users_job, cleanup_stale_records_job]

    # Cron: run every 15 minutes
    cron_jobs = [
        # arq.cron is imported lazily below to avoid hard dependency at import time
    ]

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        """Initialise the DI container once per worker process."""
        from src.config.container import Container
        from src.config.settings import get_settings
        from src.infrastructure.security.token_encryption import set_encryption_key

        settings = get_settings()
        set_encryption_key(settings.app_secret_key)
        ctx["container"] = Container(settings)
        logger.info("ARQ worker started")

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        container = ctx.get("container")
        if container:
            await container.shutdown()
        logger.info("ARQ worker shut down")

    redis_settings = None  # Set dynamically in _build_redis_settings()


def _build_redis_settings():  # type: ignore[no-untyped-def]
    """Build ARQ RedisSettings from environment.

    Returns a default localhost RedisSettings if the ``arq`` package is
    available (even when REDIS_URL is not set), so the ARQ worker CLI doesn't
    crash with ``redis_settings=None``.  The worker will discover at runtime
    whether Redis is actually reachable and log accordingly.
    """
    import os

    try:
        from arq.connections import RedisSettings

        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return RedisSettings.from_dsn(url)
    except ImportError:
        # arq not installed — worker CLI won't be used; None is safe here
        return None
    except Exception:
        # Bad URL or other config error — fall back to localhost defaults
        try:
            from arq.connections import RedisSettings as _RS

            return _RS()
        except Exception:
            return None


WorkerSettings.redis_settings = _build_redis_settings()  # type: ignore[assignment]

# Wire cron after class definition (avoids circular import at module load)
try:
    from arq import cron

    WorkerSettings.cron_jobs = [  # type: ignore[assignment]
        cron(scan_all_users_job, minute={0, 15, 30, 45}, kwargs={"since_hours": 72}),
        # Daily at 03:00 UTC: prune stale stripe events (>30d) and expired invites
        cron(cleanup_stale_records_job, hour=3, minute=0),
    ]
except ImportError:
    pass  # ARQ not installed — cron runs via in-process fallback


# ---------------------------------------------------------------------------
# In-process fallback — used when ARQ/Redis is not available (dev mode) or
# as a companion scheduler that enqueues the ARQ job each interval.
# ---------------------------------------------------------------------------


class EmailScannerWorker:
    """
    Hybrid email scanner.

    When ARQ + Redis are available: enqueues ``scan_all_users_job`` into Redis
    every scan_interval_minutes so the ARQ worker picks it up durably.

    When ARQ is unavailable (dev / no Redis): falls back to running the scan
    directly inside the FastAPI process, preserving the previous behaviour.
    """

    def __init__(
        self,
        container: Any,
        scan_interval_minutes: int = 15,
        scan_window_hours: int = 72,
        initial_scan_hours: int = 72,
    ) -> None:
        self._container = container
        self._interval = scan_interval_minutes * 60
        self._scan_window_hours = scan_window_hours
        self._initial_scan_hours = initial_scan_hours
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Email scanner started (interval=%d min)", self._interval // 60)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Email scanner stopped")

    async def _run_loop(self) -> None:
        await asyncio.sleep(30)  # Let app fully start
        first_run = True
        while self._running:
            hours = self._initial_scan_hours if first_run else self._scan_window_hours
            first_run = False
            try:
                if await self._try_enqueue_arq(hours):
                    logger.debug("Email scan job enqueued to ARQ")
                else:
                    # Direct in-process execution (dev fallback)
                    await self._scan_in_process(hours)
            except Exception as exc:
                logger.error("Email scanner error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _try_enqueue_arq(self, since_hours: int) -> bool:
        """
        Attempt to create an ARQ Redis pool and enqueue the scan job.
        Returns True on success, False if ARQ / Redis is unavailable.
        Uses a distributed lock (SET NX EX) to prevent duplicate runs across replicas.
        """
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            redis_url = getattr(self._container.settings, "redis_url", "")
            if not redis_url:
                return False

            pool = await create_pool(RedisSettings.from_dsn(redis_url))

            # Distributed lock: only one instance should trigger the scan per window
            lock_key = "email_scanner:lock"
            lock_set = await pool.set(
                lock_key,
                "1",
                nx=True,  # Only set if not already present
                ex=self._interval - 10,  # Expire just before the next window
            )
            if not lock_set:
                logger.debug("Email scan already scheduled by another instance")
                await pool.aclose()
                return True  # Another instance owns this window

            await pool.enqueue_job("scan_all_users_job", since_hours=since_hours)
            await pool.aclose()
            return True
        except Exception:
            return False

    async def _scan_in_process(self, since_hours: int) -> None:
        """Run the full scan directly (dev fallback — no ARQ needed)."""
        # Reuse the same task logic but with a fake ARQ ctx
        ctx = {"container": self._container}
        await scan_all_users_job(ctx, since_hours=since_hours)
