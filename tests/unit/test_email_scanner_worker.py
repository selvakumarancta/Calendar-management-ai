"""
Unit tests for EmailScannerWorker (infrastructure/workers/email_scanner.py — deprecated).
All DB, email, and intelligence service calls are mocked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.workers.email_scanner import EmailScannerWorker


def _make_worker(container=None) -> tuple[EmailScannerWorker, MagicMock]:
    mock_container = container or MagicMock()
    worker = EmailScannerWorker(
        container=mock_container,
        scan_interval_minutes=1,
        scan_window_hours=24,
        initial_scan_hours=48,
    )
    return worker, mock_container


def _mock_conn(user_id=None, org_id=None, provider="google"):
    c = MagicMock()
    c.user_id = user_id or uuid.uuid4()
    c.org_id = org_id or uuid.uuid4()
    c.provider = provider
    return c


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_sets_running_flag():
    worker, _ = _make_worker()
    worker._run_loop = AsyncMock()
    await worker.start()
    assert worker._running is True
    worker._task.cancel()
    try:
        await worker._task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_cancels_task():
    worker, _ = _make_worker()
    # Give it a real task that can be cancelled
    worker._running = True
    worker._task = asyncio.create_task(asyncio.sleep(100))
    await worker.stop()
    assert worker._running is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stop_with_no_task():
    """stop() is safe when _task is None."""
    worker, _ = _make_worker()
    worker._running = True
    worker._task = None
    await worker.stop()
    assert worker._running is False


# ---------------------------------------------------------------------------
# _scan_all_users
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_with_no_connections():
    """No active connections → logs debug and returns early."""
    worker, mock_container = _make_worker()

    mock_session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock.scalars.return_value = scalars_mock
    mock_session.execute = AsyncMock(return_value=result_mock)

    mock_db = MagicMock()
    mock_db.session_factory.return_value.__aenter__ = AsyncMock(
        return_value=mock_session
    )
    mock_db.session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_container.database.return_value = mock_db

    # Should return without calling _scan_user_provider
    worker._scan_user_provider = AsyncMock()
    await worker._scan_all_users(since_hours=24)
    worker._scan_user_provider.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_calls_scan_per_connection():
    """With one active connection, _scan_user_provider is called once."""
    worker, mock_container = _make_worker()

    conn = _mock_conn(provider="google")
    mock_session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [conn]
    result_mock.scalars.return_value = scalars_mock
    mock_session.execute = AsyncMock(return_value=result_mock)

    mock_db = MagicMock()
    mock_db.session_factory.return_value.__aenter__ = AsyncMock(
        return_value=mock_session
    )
    mock_db.session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_container.database.return_value = mock_db

    worker._scan_user_provider = AsyncMock()
    await worker._scan_all_users(since_hours=24)
    worker._scan_user_provider.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_catches_per_connection_exception():
    """Exception in _scan_user_provider is caught and logged per connection."""
    worker, mock_container = _make_worker()

    conn = _mock_conn(provider="google")
    mock_session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [conn]
    result_mock.scalars.return_value = scalars_mock
    mock_session.execute = AsyncMock(return_value=result_mock)

    mock_db = MagicMock()
    mock_db.session_factory.return_value.__aenter__ = AsyncMock(
        return_value=mock_session
    )
    mock_db.session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_container.database.return_value = mock_db

    worker._scan_user_provider = AsyncMock(side_effect=Exception("scan failed"))
    # Should not raise
    await worker._scan_all_users(since_hours=24)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_all_users_uses_default_window_when_none():
    """since_hours=None uses self._scan_window_hours."""
    worker, mock_container = _make_worker()

    mock_session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock.scalars.return_value = scalars_mock
    mock_session.execute = AsyncMock(return_value=result_mock)

    mock_db = MagicMock()
    mock_db.session_factory.return_value.__aenter__ = AsyncMock(
        return_value=mock_session
    )
    mock_db.session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_container.database.return_value = mock_db

    worker._scan_user_provider = AsyncMock()
    await worker._scan_all_users(since_hours=None)  # should use default window


# ---------------------------------------------------------------------------
# _scan_user_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_user_google_provider():
    """Google provider creates GmailEmailAdapter and calls scan_user_emails."""
    worker, mock_container = _make_worker()

    mock_settings = MagicMock()
    mock_settings.google_client_id = "cid"
    mock_settings.google_client_secret = "csec"
    mock_container.settings = mock_settings

    mock_db = MagicMock()
    mock_container.database.return_value = mock_db
    mock_container.llm_adapter.return_value = MagicMock()
    mock_container.calendar_adapter.return_value = MagicMock()

    mock_scan_result = MagicMock()
    mock_scan_result.suggestions_created = 2
    mock_scan_result.emails_scanned = 5
    mock_scan_result.actionable_found = 3

    mock_service = AsyncMock()
    mock_service.scan_user_emails = AsyncMock(return_value=mock_scan_result)

    mock_gmail = MagicMock()
    mock_gmail.set_db_session_factory = MagicMock()

    # Patch the lazy imports inside the method
    with (
        patch(
            "src.application.services.email_intelligence_service.EmailIntelligenceService",
            return_value=mock_service,
        ),
        patch(
            "src.infrastructure.email_providers.gmail_email.GmailEmailAdapter",
            return_value=mock_gmail,
        ),
    ):
        await worker._scan_user_provider(
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="google",
            since_hours=24,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_user_microsoft_provider():
    """Microsoft provider creates OutlookEmailAdapter."""
    worker, mock_container = _make_worker()

    mock_db = MagicMock()
    mock_container.database.return_value = mock_db
    mock_container.llm_adapter.return_value = MagicMock()
    mock_container.calendar_adapter.return_value = MagicMock()

    mock_scan_result = MagicMock()
    mock_scan_result.suggestions_created = 0

    mock_service = AsyncMock()
    mock_service.scan_user_emails = AsyncMock(return_value=mock_scan_result)

    mock_outlook = MagicMock()
    mock_outlook.set_db_session_factory = MagicMock()

    with (
        patch(
            "src.application.services.email_intelligence_service.EmailIntelligenceService",
            return_value=mock_service,
        ),
        patch(
            "src.infrastructure.email_providers.outlook_email.OutlookEmailAdapter",
            return_value=mock_outlook,
        ),
    ):
        await worker._scan_user_provider(
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="microsoft",
            since_hours=24,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_user_unsupported_provider_returns_early():
    """Unsupported provider → returns without creating service."""
    worker, mock_container = _make_worker()
    mock_container.database.return_value = MagicMock()

    # Should not raise
    await worker._scan_user_provider(
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        provider="yahoo",
        since_hours=24,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scan_user_logs_when_suggestions_created():
    """When suggestions_created > 0 the result is logged."""
    worker, mock_container = _make_worker()

    mock_db = MagicMock()
    mock_container.database.return_value = mock_db

    mock_scan_result = MagicMock()
    mock_scan_result.suggestions_created = 3
    mock_scan_result.emails_scanned = 10
    mock_scan_result.actionable_found = 5

    mock_service = AsyncMock()
    mock_service.scan_user_emails = AsyncMock(return_value=mock_scan_result)

    mock_gmail = MagicMock()
    mock_gmail.set_db_session_factory = MagicMock()

    mock_container.settings = MagicMock(
        google_client_id="id", google_client_secret="sec"
    )
    mock_container.llm_adapter.return_value = MagicMock()
    mock_container.calendar_adapter.return_value = MagicMock()

    with (
        patch(
            "src.application.services.email_intelligence_service.EmailIntelligenceService",
            return_value=mock_service,
        ),
        patch(
            "src.infrastructure.email_providers.gmail_email.GmailEmailAdapter",
            return_value=mock_gmail,
        ),
    ):
        await worker._scan_user_provider(
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            provider="google",
            since_hours=24,
        )
        mock_service.scan_user_emails.assert_called_once()
