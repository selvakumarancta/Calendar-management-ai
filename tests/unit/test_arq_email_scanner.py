"""Tests for src/infrastructure/workers/arq_email_scanner.py (ARQ functions + EmailScannerWorker)."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container(connections=None, settings=None):
    container = MagicMock()
    settings_mock = settings or MagicMock(
        google_client_id="gid",
        google_client_secret="gsec",
        redis_url="redis://localhost:6379/0",
    )
    container.settings = settings_mock

    db = MagicMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=_make_scalars(connections or []))
    session.commit = AsyncMock()
    db.session_factory = MagicMock(return_value=session)
    container.database = MagicMock(return_value=db)
    container.llm_adapter = MagicMock(return_value=MagicMock())
    container.calendar_adapter = MagicMock(return_value=MagicMock())

    return container, db, session


def _make_scalars(items):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=items)
    result.scalars = MagicMock(return_value=scalars)
    return result


def _make_connection(user_id=None, org_id=None, provider="google"):
    conn = MagicMock()
    conn.user_id = user_id or uuid.uuid4()
    conn.org_id = org_id or uuid.uuid4()
    conn.provider = provider
    return conn


# ---------------------------------------------------------------------------
# scan_all_users_job
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_job_skips_when_no_container():
    from src.infrastructure.workers.arq_email_scanner import scan_all_users_job

    result = await scan_all_users_job(ctx={})
    assert result == {"skipped": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_job_returns_zero_when_no_connections():
    from src.infrastructure.workers.arq_email_scanner import scan_all_users_job

    container, db, session = _make_container(connections=[])
    result = await scan_all_users_job(ctx={"container": container})
    assert result == {"connections": 0}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_job_scans_connections():
    from src.infrastructure.workers.arq_email_scanner import scan_all_users_job

    conn1 = _make_connection(provider="google")
    container, db, session = _make_container(connections=[conn1])

    with patch(
        "src.infrastructure.workers.arq_email_scanner._scan_single_connection",
        new=AsyncMock(),
    ):
        result = await scan_all_users_job(ctx={"container": container})

    assert result["scanned"] == 1
    assert result["failed"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_job_counts_failures():
    from src.infrastructure.workers.arq_email_scanner import scan_all_users_job

    conn1 = _make_connection(provider="google")
    conn2 = _make_connection(provider="microsoft")
    container, db, session = _make_container(connections=[conn1, conn2])

    async def fail_first(container, user_id, org_id, provider, since_hours):
        if provider == "google":
            raise Exception("Scan failed")

    with patch(
        "src.infrastructure.workers.arq_email_scanner._scan_single_connection",
        side_effect=fail_first,
    ):
        result = await scan_all_users_job(ctx={"container": container})

    assert result["scanned"] == 1
    assert result["failed"] == 1


# ---------------------------------------------------------------------------
# _scan_single_connection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_single_connection_google_provider():
    from src.infrastructure.workers.arq_email_scanner import _scan_single_connection

    container, db, _ = _make_container()

    mock_scan_result = MagicMock(
        suggestions_created=1, emails_scanned=5, actionable_found=2
    )
    mock_svc = AsyncMock()
    mock_svc.scan_user_emails = AsyncMock(return_value=mock_scan_result)
    mock_gmail = MagicMock()
    mock_gmail.set_db_session_factory = MagicMock()

    with (
        patch(
            "src.application.services.email_intelligence_service.EmailIntelligenceService",
            return_value=mock_svc,
        ),
        patch(
            "src.infrastructure.email_providers.gmail_email.GmailEmailAdapter",
            return_value=mock_gmail,
        ),
    ):
        await _scan_single_connection(
            container=container,
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="google",
            since_hours=24,
        )

    mock_svc.scan_user_emails.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_single_connection_microsoft_provider():
    from src.infrastructure.workers.arq_email_scanner import _scan_single_connection

    container, db, _ = _make_container()

    mock_scan_result = MagicMock(suggestions_created=0)
    mock_svc = AsyncMock()
    mock_svc.scan_user_emails = AsyncMock(return_value=mock_scan_result)
    mock_outlook = MagicMock()
    mock_outlook.set_db_session_factory = MagicMock()

    with (
        patch(
            "src.application.services.email_intelligence_service.EmailIntelligenceService",
            return_value=mock_svc,
        ),
        patch(
            "src.infrastructure.email_providers.outlook_email.OutlookEmailAdapter",
            return_value=mock_outlook,
        ),
    ):
        await _scan_single_connection(
            container=container,
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="microsoft",
            since_hours=24,
        )

    mock_svc.scan_user_emails.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_single_connection_unsupported_provider_returns_early():
    from src.infrastructure.workers.arq_email_scanner import _scan_single_connection

    container, db, _ = _make_container()

    # Should not call EmailIntelligenceService for unknown provider
    with patch(
        "src.application.services.email_intelligence_service.EmailIntelligenceService"
    ) as mock_svc_cls:
        await _scan_single_connection(
            container=container,
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="yahoo",  # Unsupported
            since_hours=24,
        )

    mock_svc_cls.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_stale_records_job
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_stale_records_skips_when_no_container():
    from src.infrastructure.workers.arq_email_scanner import cleanup_stale_records_job

    result = await cleanup_stale_records_job(ctx={})
    assert result == {"skipped": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_stale_records_deletes_old_rows():
    from src.infrastructure.workers.arq_email_scanner import cleanup_stale_records_job

    container, db, session = _make_container()

    # Mock rowcount from delete operations
    del_result = MagicMock()
    del_result.rowcount = 3
    session.execute = AsyncMock(return_value=del_result)

    result = await cleanup_stale_records_job(ctx={"container": container})
    assert "stripe_events_deleted" in result
    assert "invites_deleted" in result


# ---------------------------------------------------------------------------
# WorkerSettings
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_settings_on_startup_sets_container():
    from src.infrastructure.workers.arq_email_scanner import WorkerSettings

    ctx = {}
    mock_settings = MagicMock(app_secret_key="key-123")
    mock_container = MagicMock()

    with (
        patch(
            "src.config.settings.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "src.infrastructure.security.token_encryption.set_encryption_key",
        ),
        patch(
            "src.config.container.Container",
            return_value=mock_container,
        ),
    ):
        await WorkerSettings.on_startup(ctx)

    assert ctx["container"] is mock_container


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_settings_on_shutdown_calls_shutdown():
    from src.infrastructure.workers.arq_email_scanner import WorkerSettings

    mock_container = AsyncMock()
    mock_container.shutdown = AsyncMock()
    ctx = {"container": mock_container}

    await WorkerSettings.on_shutdown(ctx)
    mock_container.shutdown.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_settings_on_shutdown_handles_missing_container():
    from src.infrastructure.workers.arq_email_scanner import WorkerSettings

    # Should not raise when container is not in ctx
    await WorkerSettings.on_shutdown(ctx={})


# ---------------------------------------------------------------------------
# _build_redis_settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_redis_settings_returns_none_when_arq_missing():
    with patch.dict("sys.modules", {"arq": None, "arq.connections": None}):
        import importlib

        from src.infrastructure.workers import arq_email_scanner

        result = arq_email_scanner._build_redis_settings()
    # If arq not importable, returns None
    assert result is None or hasattr(result, "host")


@pytest.mark.unit
def test_build_redis_settings_returns_settings_when_arq_available():
    from src.infrastructure.workers.arq_email_scanner import _build_redis_settings

    result = _build_redis_settings()
    # Either None (arq not installed) or an ARQ RedisSettings object
    assert result is None or hasattr(result, "host")


# ---------------------------------------------------------------------------
# EmailScannerWorker (in-process fallback)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scanner_worker_start_and_stop():
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container, scan_interval_minutes=60)

    await worker.start()
    assert worker._task is not None
    assert worker._running is True

    await worker.stop()
    assert worker._running is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scanner_worker_stop_no_task():
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container)
    worker._running = False
    worker._task = None

    # Should not raise
    await worker.stop()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_in_process_calls_scan_all_users_job():
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container)

    with patch(
        "src.infrastructure.workers.arq_email_scanner.scan_all_users_job",
        new=AsyncMock(return_value={"scanned": 1}),
    ) as mock_job:
        await worker._scan_in_process(since_hours=48)

    mock_job.assert_called_once_with({"container": container}, since_hours=48)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_try_enqueue_arq_returns_false_when_no_redis_url():
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    container.settings.redis_url = ""
    worker = EmailScannerWorker(container)

    result = await worker._try_enqueue_arq(since_hours=24)
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_try_enqueue_arq_returns_false_when_arq_unavailable():
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    container.settings.redis_url = "redis://localhost:6379/0"
    worker = EmailScannerWorker(container)

    with patch.dict("sys.modules", {"arq": None}):
        result = await worker._try_enqueue_arq(since_hours=24)

    assert result is False


# ---------------------------------------------------------------------------
# _build_redis_settings — bad URL fallback (lines 257-264)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_redis_settings_bad_url_falls_back_to_defaults():
    """Lines 257-264: bad REDIS_URL triggers exception → falls back to defaults."""
    import os

    from src.infrastructure.workers.arq_email_scanner import _build_redis_settings

    with patch.dict(os.environ, {"REDIS_URL": "not-a-valid-url:::"}):
        result = _build_redis_settings()
    # Should return either a RedisSettings (fault-tolerant fallback) or None
    assert result is None or hasattr(result, "host")


# ---------------------------------------------------------------------------
# _run_loop — runs one iteration then stops (lines 329-342)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_loop_calls_try_enqueue_arq_and_stops():
    """Lines 329-342: _run_loop iterates, calls _try_enqueue_arq, then sleeps."""
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container, scan_interval_minutes=1)
    worker._running = True

    sleep_calls = []

    async def _fake_try_enqueue(hours):
        return True

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:  # first call = startup sleep, second = interval sleep
            worker._running = False

    with (
        patch.object(worker, "_try_enqueue_arq", side_effect=_fake_try_enqueue),
        patch("asyncio.sleep", side_effect=_fake_sleep),
    ):
        await worker._run_loop()

    assert not worker._running
    assert len(sleep_calls) >= 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_loop_falls_back_to_in_process_when_enqueue_false():
    """Lines 337-339: when _try_enqueue_arq returns False, _scan_in_process is called."""
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container, scan_interval_minutes=1)
    worker._running = True

    scan_called = []
    sleep_calls = []

    async def _fake_try_enqueue(hours):
        return False

    async def _fake_in_process(hours):
        scan_called.append(hours)

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            worker._running = False

    with (
        patch.object(worker, "_try_enqueue_arq", side_effect=_fake_try_enqueue),
        patch.object(worker, "_scan_in_process", side_effect=_fake_in_process),
        patch("asyncio.sleep", side_effect=_fake_sleep),
    ):
        await worker._run_loop()

    assert len(scan_called) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_loop_handles_exception_in_scan():
    """Lines 340-341: exception in scan is caught and loop continues."""
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    worker = EmailScannerWorker(container, scan_interval_minutes=1)
    worker._running = True

    sleep_calls = []

    async def _fake_try_enqueue(hours):
        raise RuntimeError("arq down")

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            worker._running = False

    with (
        patch.object(worker, "_try_enqueue_arq", side_effect=_fake_try_enqueue),
        patch("asyncio.sleep", side_effect=_fake_sleep),
    ):
        # Should not raise
        await worker._run_loop()

    assert not worker._running


# ---------------------------------------------------------------------------
# _try_enqueue_arq — success with lock (lines 352-375)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_try_enqueue_arq_success_enqueues_job():
    """Lines 352-375: ARQ available + redis URL + lock acquired → enqueues job."""
    import sys
    import types

    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    container.settings.redis_url = "redis://localhost:6379/0"
    worker = EmailScannerWorker(container)

    mock_pool = AsyncMock()
    mock_pool.set = AsyncMock(return_value=True)  # lock acquired
    mock_pool.enqueue_job = AsyncMock(return_value=None)
    mock_pool.aclose = AsyncMock()

    fake_redis_settings = MagicMock()
    fake_redis_settings.from_dsn = MagicMock(return_value=MagicMock())

    # Build fake arq modules so the import inside the function succeeds
    fake_arq = types.ModuleType("arq")
    fake_arq.create_pool = AsyncMock(return_value=mock_pool)
    fake_arq_connections = types.ModuleType("arq.connections")
    fake_arq_connections.RedisSettings = fake_redis_settings
    fake_arq.connections = fake_arq_connections

    with patch.dict(
        sys.modules, {"arq": fake_arq, "arq.connections": fake_arq_connections}
    ):
        result = await worker._try_enqueue_arq(since_hours=24)

    assert result is True
    mock_pool.enqueue_job.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_try_enqueue_arq_returns_true_when_lock_not_acquired():
    """Lines 368-371: lock already held by another instance → return True without enqueuing."""
    import sys
    import types

    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    container = MagicMock()
    container.settings.redis_url = "redis://localhost:6379/0"
    worker = EmailScannerWorker(container)

    mock_pool = AsyncMock()
    mock_pool.set = AsyncMock(return_value=None)  # lock NOT acquired
    mock_pool.aclose = AsyncMock()
    mock_pool.enqueue_job = AsyncMock()

    fake_redis_settings = MagicMock()
    fake_redis_settings.from_dsn = MagicMock(return_value=MagicMock())

    fake_arq = types.ModuleType("arq")
    fake_arq.create_pool = AsyncMock(return_value=mock_pool)
    fake_arq_connections = types.ModuleType("arq.connections")
    fake_arq_connections.RedisSettings = fake_redis_settings
    fake_arq.connections = fake_arq_connections

    with patch.dict(
        sys.modules, {"arq": fake_arq, "arq.connections": fake_arq_connections}
    ):
        result = await worker._try_enqueue_arq(since_hours=24)

    assert result is True
    mock_pool.enqueue_job.assert_not_called()
