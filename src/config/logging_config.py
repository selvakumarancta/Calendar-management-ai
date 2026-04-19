"""
Logging configuration — wires structlog with JSON output in production,
pretty console output in development, and injects the correlation request ID
into every log record automatically.
"""

from __future__ import annotations

import logging
import logging.config

import structlog


def configure_logging(app_env: str = "development", log_level: str = "INFO") -> None:
    """
    Configure structlog + stdlib logging.

    Call this once at application startup before any loggers are used.
    Production: JSON lines (structured, machine-readable).
    Development: Coloured console output.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors pipeline
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _inject_request_id,
    ]

    if app_env == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _inject_request_id(logger: object, method: str, event_dict: dict) -> dict:
    """Structlog processor that injects the current X-Request-ID."""
    from src.api.middleware.correlation_id import request_id_var

    req_id = request_id_var.get("")
    if req_id:
        event_dict["request_id"] = req_id
    return event_dict
