"""
Integration test for the FastAPI application.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.rest.app import create_app


@pytest.fixture()
async def app():
    """Create a test app with container wired on app.state and tables created."""
    from src.config.container import Container
    from src.config.settings import Settings
    from src.infrastructure.security.token_encryption import set_encryption_key

    application = create_app()
    settings = Settings()
    set_encryption_key(settings.app_secret_key)

    container = Container(settings)
    # Create tables for the test run (lifespan is not triggered by ASGITransport)
    db = container.database()
    await db.create_tables()

    application.state.container = container
    yield application
    await container.shutdown()


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
