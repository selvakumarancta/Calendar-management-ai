"""
FastAPI Application Factory — creates the main API application.
Wires the DI container, exception handlers, and all routers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.middleware.correlation_id import CorrelationIdMiddleware
from src.api.middleware.rate_limiter import RateLimiterMiddleware
from src.api.rest.email_routes import email_router
from src.api.rest.org_routes import google_callback_router, org_router
from src.api.rest.admin_routes import admin_rbac_router
from src.api.rest.routes import (
    admin_router,
    auth_router,
    calendar_router,
    chat_router,
    health_router,
)
from src.api.rest.settings_routes import settings_router
from src.api.rest.whatsapp_routes import whatsapp_router
from src.config.container import Container
from src.config.logging_config import configure_logging
from src.config.settings import get_settings
from src.domain.exceptions import (
    AgentError,
    AuthenticationError,
    CalendarProviderError,
    DomainError,
    EventConflictError,
    EventNotFoundError,
    InsufficientPermissionsError,
    QuotaExceededError,
)

logger = logging.getLogger("calendar_agent")


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — create DI container on startup, teardown on shutdown."""
    settings = get_settings()

    # Guard: refuse to start in production with insecure defaults
    if settings.is_production:
        if settings.app_secret_key in ("change-me", "", "secret", "dev"):
            raise RuntimeError(
                "APP_SECRET_KEY must be changed from the default value before running in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if not settings.active_api_key:
            raise RuntimeError(
                f"LLM API key ({settings.llm_provider.upper()}_API_KEY) must be set in production."
            )

    container = Container(settings)

    # Initialize Sentry error tracking (no-op if DSN not configured or invalid)
    if (
        settings.sentry_dsn
        and not settings.sentry_dsn.startswith("...")
        and "ingest.sentry.io" in settings.sentry_dsn
    ):
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.app_env,
                integrations=[FastApiIntegration(), SqlalchemyIntegration()],
                traces_sample_rate=0.1,
                send_default_pii=False,
            )
            logger.info("Sentry error tracking initialized")
        except Exception as sentry_err:
            logger.warning("Sentry init failed (ignored): %s", sentry_err)

    # Initialize token encryption with the app secret key
    from src.infrastructure.security.token_encryption import set_encryption_key

    set_encryption_key(settings.app_secret_key)

    # Auto-create tables in development (prod should use Alembic)
    if settings.is_development:
        db = container.database()
        await db.create_tables()
        logger.info("Development mode — auto-created database tables")

    # Apply DB-backed config_settings overrides to the in-memory Settings object
    try:
        async with container.database().session_factory() as session:
            from sqlalchemy import select

            from src.infrastructure.persistence.config_model import ConfigSettingModel

            rows = (await session.execute(select(ConfigSettingModel))).scalars().all()
            for row in rows:
                if hasattr(settings, row.key):
                    object.__setattr__(settings, row.key, row.value)
            if rows:
                logger.info("Loaded %d config overrides from DB", len(rows))
    except Exception as exc:
        logger.warning("Could not load DB config overrides: %s", exc)

    # Store on app.state so dependencies.get_container() can find it
    app.state.container = container

    # Start background email scanner (ARQ when Redis available, in-process fallback)
    from src.infrastructure.workers.arq_email_scanner import EmailScannerWorker

    scanner = EmailScannerWorker(
        container,
        scan_interval_minutes=settings.email_scan_interval_minutes,
        scan_window_hours=settings.email_scan_window_hours,
        initial_scan_hours=settings.email_scan_initial_hours,
    )
    app.state.email_scanner = scanner
    await scanner.start()

    # Start background Google token refresh loop so access tokens are always
    # kept alive — this is the permanent solution to "event not in calendar
    # after approval" caused by an expired access token.
    import asyncio

    async def _token_refresh_loop() -> None:
        """Proactively refresh all active Google tokens every 30 minutes.

        Google access tokens expire after 60 minutes.  Refreshing at -30 min
        guarantees every request always has at least 30 minutes of headroom,
        regardless of how long the user was idle.
        """
        REFRESH_INTERVAL_S = 30 * 60  # 30 minutes
        while True:
            await asyncio.sleep(REFRESH_INTERVAL_S)
            try:
                db = container.database()
                cal = container.calendar_adapter()
                from sqlalchemy import select

                from src.infrastructure.persistence.org_models import (
                    ProviderConnectionModel,
                )

                async with db.session_factory() as session:
                    result = await session.execute(
                        select(ProviderConnectionModel.user_id)
                        .where(
                            ProviderConnectionModel.provider == "google",
                            ProviderConnectionModel.status == "active",
                            ProviderConnectionModel.access_token != "dev-token",
                        )
                        .distinct()
                    )
                    user_ids = [r[0] for r in result.fetchall()]

                refreshed = 0
                for uid in user_ids:
                    try:
                        tokens = await cal._get_google_tokens(uid)
                        if tokens:
                            refreshed += 1
                    except Exception as _e:
                        logger.debug("Token refresh loop error for %s: %s", uid, _e)
                if user_ids:
                    logger.info(
                        "Token refresh loop: refreshed %d/%d Google tokens",
                        refreshed,
                        len(user_ids),
                    )
            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.warning("Token refresh loop error (non-fatal): %s", loop_err)

    refresh_task = asyncio.create_task(_token_refresh_loop())
    app.state.token_refresh_task = refresh_task

    logger.info("🚀 Calendar Agent starting in %s mode", settings.app_env)
    yield
    # Shutdown
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass
    await scanner.stop()
    await container.shutdown()
    logger.info("👋 Calendar Agent shut down")


# ---------------------------------------------------------------------------
# Exception → HTTP mapping
# ---------------------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to structured JSON error responses."""

    @app.exception_handler(EventNotFoundError)
    async def _event_not_found(_req: Request, exc: EventNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(EventConflictError)
    async def _event_conflict(_req: Request, exc: EventConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.message})

    @app.exception_handler(QuotaExceededError)
    async def _quota_exceeded(_req: Request, exc: QuotaExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": exc.message})

    @app.exception_handler(AuthenticationError)
    async def _auth_error(_req: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": exc.message})

    @app.exception_handler(InsufficientPermissionsError)
    async def _forbidden(
        _req: Request, exc: InsufficientPermissionsError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": exc.message})

    @app.exception_handler(CalendarProviderError)
    async def _provider_error(
        _req: Request, exc: CalendarProviderError
    ) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": exc.message})

    @app.exception_handler(AgentError)
    async def _agent_error(_req: Request, exc: AgentError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": exc.message})

    # Catch-all for any remaining DomainError subclasses
    @app.exception_handler(DomainError)
    async def _domain_error(_req: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.message})


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """FastAPI application factory."""
    settings = get_settings()

    # Configure structured logging early so all startup messages are structured
    configure_logging(app_env=settings.app_env, log_level=settings.app_log_level)

    app = FastAPI(
        title="Calendar Management Agent",
        description="AI-powered Calendar Management SaaS Platform",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Correlation ID — must be added before rate limiter so request_id propagates
    app.add_middleware(CorrelationIdMiddleware)

    # Rate limiting (uses Redis in prod, in-memory in dev)
    app.add_middleware(RateLimiterMiddleware)

    # Prometheus metrics — exposes /metrics for Prometheus scraping.
    # No-ops gracefully if the package is absent (e.g. stripped prod images).
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=False,
            excluded_handlers=["/health", "/ready", "/nginx-health", "/metrics"],
        ).instrument(app).expose(app, include_in_schema=False)
    except ImportError:
        pass

    # Exception handlers
    _register_exception_handlers(app)

    # Routers
    app.include_router(health_router, tags=["Health"])
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["Chat"])
    app.include_router(calendar_router, prefix="/api/v1/calendar", tags=["Calendar"])
    app.include_router(
        google_callback_router, prefix="/api/v1/orgs", tags=["Organizations"]
    )
    app.include_router(org_router, prefix="/api/v1/orgs", tags=["Organizations"])
    app.include_router(settings_router, prefix="/api/v1/settings", tags=["Settings"])
    app.include_router(
        email_router, prefix="/api/v1/email", tags=["Email Intelligence"]
    )
    app.include_router(whatsapp_router, prefix="/api/v1/webhooks", tags=["WhatsApp"])
    app.include_router(admin_router, prefix="/api/v1/admin", tags=["Admin"])
    app.include_router(admin_rbac_router, prefix="/api/v1/admin/rbac", tags=["Admin RBAC"])

    # Billing routes
    from src.api.rest.billing_routes import billing_router

    app.include_router(billing_router, prefix="/api/v1/billing", tags=["Billing"])

    # WebSocket
    from src.api.websocket.chat_ws import ws_router

    app.include_router(ws_router)

    # --- Static frontend -------------------------------------------------
    _static_dir = Path(__file__).resolve().parent.parent.parent.parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def _spa_root() -> FileResponse:
            return FileResponse(str(_static_dir / "index.html"))

        # ── Public scheduling pages (no auth required) ──────────────────
        _schedule_html = _static_dir / "schedule.html"
        if _schedule_html.exists():
            @app.get("/schedule/{link_id}", include_in_schema=False)
            async def _schedule_page(link_id: str) -> FileResponse:  # noqa: ARG001
                return FileResponse(str(_schedule_html))

            @app.get("/book/{username}", include_in_schema=False)
            async def _book_page(username: str) -> FileResponse:  # noqa: ARG001
                return FileResponse(str(_schedule_html))

    # ── Persistent personal booking API (no auth) ───────────────────────────
    from src.api.rest.booking_routes import booking_router
    app.include_router(booking_router, prefix="/api/v1/public", tags=["Public Booking"])

    return app


# Module-level app instance for uvicorn (e.g. uvicorn src.api.rest.app:app)
app = create_app()
