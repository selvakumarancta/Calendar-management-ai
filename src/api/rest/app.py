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

from src.api.middleware.rate_limiter import RateLimiterMiddleware
from src.api.rest.email_routes import email_router
from src.api.rest.org_routes import google_callback_router, org_router
from src.api.rest.routes import auth_router, calendar_router, chat_router, health_router
from src.api.rest.settings_routes import settings_router
from src.config.container import Container
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
    container = Container(settings)

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

    # Start background email scanner
    from src.infrastructure.workers.email_scanner import EmailScannerWorker

    scanner = EmailScannerWorker(
        container,
        scan_interval_minutes=settings.email_scan_interval_minutes,
        scan_window_hours=settings.email_scan_window_hours,
        initial_scan_hours=settings.email_scan_initial_hours,
    )
    app.state.email_scanner = scanner
    await scanner.start()

    logger.info("🚀 Calendar Agent starting in %s mode", settings.app_env)
    yield
    # Shutdown
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

    # Rate limiting
    app.add_middleware(RateLimiterMiddleware)

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

    return app


# Module-level app instance for uvicorn (e.g. uvicorn src.api.rest.app:app)
app = create_app()
