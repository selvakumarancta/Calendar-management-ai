"""
Rate Limiter Middleware — per-tenant request throttling.
Uses in-memory store for dev, Redis for production.
"""

from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple sliding-window rate limiter middleware."""

    # Per-IP limits (in production, this would be per-tenant via Redis)
    RATE_LIMIT = 60  # requests
    WINDOW_SECONDS = 60  # per minute

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: object) -> object:
        # Skip rate limiting for health checks
        if request.url.path in ("/health", "/ready"):
            return await call_next(request)  # type: ignore[misc]

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self.WINDOW_SECONDS
        ]

        if len(self._requests[client_ip]) >= self.RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": self.WINDOW_SECONDS,
                },
            )

        self._requests[client_ip].append(now)
        return await call_next(request)  # type: ignore[misc]
