"""
Integration test for the FastAPI application.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.rest.app import create_app


@pytest.fixture()
def app():
    """Create a test app with container wired on app.state."""
    from src.config.container import Container
    from src.config.settings import Settings

    application = create_app()
    # Wire container manually since ASGI lifespan doesn't run under httpx
    settings = Settings()
    container = Container(settings)
    application.state.container = container
    return application


class TestHealthEndpoints:
    """Test health check endpoints."""

    @pytest.mark.integration
    async def test_health_check(self, app) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

    @pytest.mark.integration
    async def test_readiness_check(self, app) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")
            # May return 200 or 503 depending on DB availability
            assert response.status_code in (200, 503)

    @pytest.mark.integration
    async def test_chat_endpoint_requires_auth(self, app) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat/",
                json={"message": "What's on my calendar today?"},
            )
            # Should require authentication now
            assert response.status_code == 401

    @pytest.mark.integration
    async def test_profile_requires_auth(self, app) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/auth/me")
            assert response.status_code == 401
