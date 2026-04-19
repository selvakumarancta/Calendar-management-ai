"""
Correlation ID Middleware — injects a unique request ID into every request/response.
Enables cross-service log tracing.  Consumers read it via contextvars.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Module-level ContextVar so handlers/services can read the current request ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
# Client IP extracted from the incoming request (set by correlation middleware)
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="")

HEADER_NAME = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a ``X-Request-ID`` header to every request and response."""

    async def dispatch(self, request: Request, call_next: object) -> object:  # type: ignore[override]
        req_id = request.headers.get(HEADER_NAME) or str(uuid.uuid4())
        client_ip = request.client.host if request.client else ""
        id_token = request_id_var.set(req_id)
        ip_token = client_ip_var.set(client_ip)
        try:
            response = await call_next(request)  # type: ignore[misc]
            response.headers[HEADER_NAME] = req_id
            return response
        finally:
            request_id_var.reset(id_token)
            client_ip_var.reset(ip_token)
