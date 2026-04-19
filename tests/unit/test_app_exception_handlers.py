"""
Tests for src/api/rest/app.py — exception handlers.

Tests exception handlers by creating a minimal FastAPI test app
that raises each domain exception and verifying the HTTP response codes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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


def _build_exception_test_app() -> FastAPI:
    """Create a minimal app with the exception handlers from create_app."""
    from src.api.rest.app import _register_exception_handlers

    app = FastAPI()
    _register_exception_handlers(app)

    @app.get("/raise/event-not-found")
    async def _raise_event_not_found():
        raise EventNotFoundError("Event not found")

    @app.get("/raise/event-conflict")
    async def _raise_event_conflict():
        raise EventConflictError("Event conflict")

    @app.get("/raise/quota-exceeded")
    async def _raise_quota_exceeded():
        raise QuotaExceededError("Quota exceeded")

    @app.get("/raise/auth-error")
    async def _raise_auth_error():
        raise AuthenticationError("Auth failed")

    @app.get("/raise/permissions-error")
    async def _raise_permissions_error():
        raise InsufficientPermissionsError("Forbidden")

    @app.get("/raise/provider-error")
    async def _raise_provider_error():
        raise CalendarProviderError("Provider error")

    @app.get("/raise/agent-error")
    async def _raise_agent_error():
        raise AgentError("Agent error")

    @app.get("/raise/domain-error")
    async def _raise_domain_error():
        raise DomainError("Generic domain error")

    return app


@pytest.fixture()
async def exception_client():
    app = _build_exception_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_not_found_handler(exception_client):
    resp = await exception_client.get("/raise/event-not-found")
    assert resp.status_code == 404
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_conflict_handler(exception_client):
    resp = await exception_client.get("/raise/event-conflict")
    assert resp.status_code == 409
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_quota_exceeded_handler(exception_client):
    resp = await exception_client.get("/raise/quota-exceeded")
    assert resp.status_code == 429
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auth_error_handler(exception_client):
    resp = await exception_client.get("/raise/auth-error")
    assert resp.status_code == 401
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_permissions_error_handler(exception_client):
    resp = await exception_client.get("/raise/permissions-error")
    assert resp.status_code == 403
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_error_handler(exception_client):
    resp = await exception_client.get("/raise/provider-error")
    assert resp.status_code == 502
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_error_handler(exception_client):
    resp = await exception_client.get("/raise/agent-error")
    assert resp.status_code == 500
    assert "detail" in resp.json()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_domain_error_catch_all_handler(exception_client):
    resp = await exception_client.get("/raise/domain-error")
    assert resp.status_code == 422
    assert "detail" in resp.json()
