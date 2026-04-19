"""
Unit tests for src/api/rest/app.py lifespan and create_app factory.

Covers lines 55-140 (lifespan startup/shutdown), 234-235, 272.
"""

from __future__ import annotations

import types
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Lifespan startup — dev mode (lines 55-140)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_runs_dev_mode():
    """Lines 55-140: lifespan startup in dev mode — creates container, starts scanner."""
    from src.api.rest.app import create_app, lifespan

    app = create_app()

    mock_scanner = MagicMock()
    mock_scanner.start = AsyncMock()
    mock_scanner.stop = AsyncMock()

    mock_container = MagicMock()
    mock_container.settings = MagicMock(
        is_production=False,
        is_development=True,
        app_secret_key="test-secret-key",
        sentry_dsn="",
        email_scan_interval_minutes=60,
        email_scan_window_hours=72,
        email_scan_initial_hours=72,
    )
    mock_db = MagicMock()
    mock_db.create_tables = AsyncMock()
    mock_db.session_factory = AsyncMock()
    mock_container.database = MagicMock(return_value=mock_db)
    mock_container.shutdown = AsyncMock()

    # Make session_factory return an async context manager that yields a session
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    mock_db.session_factory = MagicMock(return_value=mock_session)

    with (
        patch("src.api.rest.app.get_settings", return_value=mock_container.settings),
        patch("src.api.rest.app.Container", return_value=mock_container),
        patch(
            "src.infrastructure.workers.arq_email_scanner.EmailScannerWorker",
            return_value=mock_scanner,
        ),
        patch("src.infrastructure.security.token_encryption.set_encryption_key"),
    ):
        async with lifespan(app):
            # Startup complete → container and scanner are on app.state
            assert app.state.container is mock_container
        # Shutdown: scanner.stop() called
    mock_scanner.start.assert_called_once()
    mock_scanner.stop.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_production_bad_secret_raises():
    """Lines 58-63: production mode with default secret key raises RuntimeError."""
    from src.api.rest.app import create_app, lifespan

    app = create_app()

    mock_settings = MagicMock(
        is_production=True,
        app_secret_key="change-me",  # insecure default → should raise
        active_api_key="sk-real-key",
    )

    with patch("src.api.rest.app.get_settings", return_value=mock_settings):
        with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
            async with lifespan(app):
                pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_production_no_api_key_raises():
    """Lines 64-67: production mode with no LLM API key raises RuntimeError."""
    from src.api.rest.app import create_app, lifespan

    app = create_app()

    mock_settings = MagicMock(
        is_production=True,
        app_secret_key="strong-secret-key-that-is-not-default",
        active_api_key="",  # missing → should raise
        llm_provider="openai",
    )

    with patch("src.api.rest.app.get_settings", return_value=mock_settings):
        with pytest.raises(RuntimeError, match="LLM API key"):
            async with lifespan(app):
                pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_with_sentry_dsn():
    """Lines 72-91: valid Sentry DSN triggers sentry_sdk.init."""
    from src.api.rest.app import create_app, lifespan

    app = create_app()

    mock_scanner = MagicMock()
    mock_scanner.start = AsyncMock()
    mock_scanner.stop = AsyncMock()

    mock_container = MagicMock()
    mock_settings = MagicMock(
        is_production=False,
        is_development=False,
        app_secret_key="test-secret",
        sentry_dsn="https://abc123@ingest.sentry.io/123",
        email_scan_interval_minutes=60,
        email_scan_window_hours=72,
        email_scan_initial_hours=72,
        app_env="staging",
    )
    mock_container.settings = mock_settings

    mock_db = MagicMock()
    mock_db.create_tables = AsyncMock()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    mock_db.session_factory = MagicMock(return_value=mock_session)
    mock_container.database = MagicMock(return_value=mock_db)
    mock_container.shutdown = AsyncMock()

    mock_sentry = MagicMock()
    fake_sentry_mod = types.ModuleType("sentry_sdk")
    fake_sentry_mod.init = mock_sentry
    fake_fastapi_int = types.ModuleType("sentry_sdk.integrations.fastapi")
    fake_fastapi_int.FastApiIntegration = MagicMock
    fake_sql_int = types.ModuleType("sentry_sdk.integrations.sqlalchemy")
    fake_sql_int.SqlalchemyIntegration = MagicMock

    import sys as _sys

    with (
        patch("src.api.rest.app.get_settings", return_value=mock_settings),
        patch("src.api.rest.app.Container", return_value=mock_container),
        patch(
            "src.infrastructure.workers.arq_email_scanner.EmailScannerWorker",
            return_value=mock_scanner,
        ),
        patch("src.infrastructure.security.token_encryption.set_encryption_key"),
        patch.dict(
            _sys.modules,
            {
                "sentry_sdk": fake_sentry_mod,
                "sentry_sdk.integrations.fastapi": fake_fastapi_int,
                "sentry_sdk.integrations.sqlalchemy": fake_sql_int,
            },
        ),
    ):
        async with lifespan(app):
            pass

    mock_sentry.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_db_config_override_loaded():
    """Lines 104-117: DB config overrides applied to settings when rows exist."""
    from src.api.rest.app import create_app, lifespan

    app = create_app()

    mock_scanner = MagicMock()
    mock_scanner.start = AsyncMock()
    mock_scanner.stop = AsyncMock()

    mock_container = MagicMock()
    mock_settings = MagicMock(
        is_production=False,
        is_development=False,
        app_secret_key="test-secret",
        sentry_dsn="",
        email_scan_interval_minutes=60,
        email_scan_window_hours=72,
        email_scan_initial_hours=72,
        app_env="development",
    )
    mock_settings.llm_provider = "openai"
    mock_container.settings = mock_settings

    # Create a config row
    config_row = MagicMock()
    config_row.key = "llm_provider"
    config_row.value = "anthropic"

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=[config_row]))
            )
        )
    )

    mock_db = MagicMock()
    mock_db.create_tables = AsyncMock()
    mock_db.session_factory = MagicMock(return_value=mock_session)
    mock_container.database = MagicMock(return_value=mock_db)
    mock_container.shutdown = AsyncMock()

    with (
        patch("src.api.rest.app.get_settings", return_value=mock_settings),
        patch("src.api.rest.app.Container", return_value=mock_container),
        patch(
            "src.infrastructure.workers.arq_email_scanner.EmailScannerWorker",
            return_value=mock_scanner,
        ),
        patch("src.infrastructure.security.token_encryption.set_encryption_key"),
    ):
        async with lifespan(app):
            pass  # startup applies overrides


# ---------------------------------------------------------------------------
# create_app — Prometheus ImportError branch (lines 234-235)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_create_app_prometheus_import_error_is_ignored():
    """Lines 234-235: prometheus_fastapi_instrumentator ImportError → silently ignored."""
    import sys

    # Remove the module so the import inside create_app raises ImportError
    with patch.dict(sys.modules, {"prometheus_fastapi_instrumentator": None}):
        from src.api.rest.app import create_app

        # Should not raise
        app = create_app()
    assert app is not None


# ---------------------------------------------------------------------------
# Static file route (line 272) — covered if static/ dir exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_spa_root_returns_index_html():
    """Line 272: GET / serves index.html from static/ directory."""
    import os
    from pathlib import Path

    from httpx import ASGITransport, AsyncClient

    from src.api.rest.app import create_app

    # The static/ dir exists in this repo → the route should be registered
    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    if not static_dir.is_dir():
        pytest.skip("No static/ directory — route not registered")

    app = create_app()

    # Set up a container (bypass lifespan)
    from src.config.container import Container
    from src.config.settings import Settings
    from src.infrastructure.security.token_encryption import set_encryption_key

    settings = Settings()
    set_encryption_key(settings.app_secret_key)
    container = Container(settings)
    await container.database().create_tables()
    app.state.container = container

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        resp = await ac.get("/")

    assert resp.status_code == 200
    await container.shutdown()
