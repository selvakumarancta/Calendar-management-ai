"""Tests for src/config/container.py — DI Container."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_settings(**kwargs):
    s = MagicMock()
    s.database_url = kwargs.get("database_url", "sqlite+aiosqlite:///test.db")
    s.is_development = kwargs.get("is_development", True)
    s.is_production = kwargs.get("is_production", False)
    s.redis_url = kwargs.get("redis_url", "redis://localhost:6379/0")
    s.google_client_id = kwargs.get("google_client_id", "gid")
    s.google_client_secret = kwargs.get("google_client_secret", "gsec")
    s.google_redirect_uri = kwargs.get(
        "google_redirect_uri", "http://localhost/callback"
    )
    s.microsoft_client_id = kwargs.get("microsoft_client_id", "mid")
    s.microsoft_client_secret = kwargs.get("microsoft_client_secret", "msec")
    s.microsoft_redirect_uri = kwargs.get(
        "microsoft_redirect_uri", "http://localhost/ms/callback"
    )
    s.microsoft_tenant_id = kwargs.get("microsoft_tenant_id", "tid")
    s.app_secret_key = kwargs.get("app_secret_key", "secret-key")
    s.jwt_algorithm = kwargs.get("jwt_algorithm", "HS256")
    s.jwt_access_token_expire_minutes = kwargs.get(
        "jwt_access_token_expire_minutes", 60
    )
    s.jwt_refresh_token_expire_days = kwargs.get("jwt_refresh_token_expire_days", 7)
    s.llm_provider = kwargs.get("llm_provider", "openai")
    s.active_api_key = kwargs.get("active_api_key", "sk-test")
    s.active_model_fast = kwargs.get("active_model_fast", "gpt-4o-mini")
    s.llm_temperature = kwargs.get("llm_temperature", 0.1)
    s.llm_max_tokens = kwargs.get("llm_max_tokens", 2048)
    s.agent_max_iterations = kwargs.get("agent_max_iterations", 10)
    s.agent_working_hours_start = kwargs.get("agent_working_hours_start", "09:00")
    s.agent_working_hours_end = kwargs.get("agent_working_hours_end", "17:00")
    return s


# ---------------------------------------------------------------------------
# _NullUserRepository
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_user_repo_get_by_id_returns_none():
    from src.config.container import _NullUserRepository

    repo = _NullUserRepository()
    assert await repo.get_by_id("any") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_user_repo_get_by_email_returns_none():
    from src.config.container import _NullUserRepository

    repo = _NullUserRepository()
    assert await repo.get_by_email("x@x.com") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_user_repo_create_returns_user():
    from src.config.container import _NullUserRepository

    user = MagicMock()
    repo = _NullUserRepository()
    assert await repo.create(user) is user


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_user_repo_update_returns_user():
    from src.config.container import _NullUserRepository

    user = MagicMock()
    repo = _NullUserRepository()
    assert await repo.update(user) is user


# ---------------------------------------------------------------------------
# Container.database
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_database_lazy_creates_instance():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_db = MagicMock()
    with patch(
        "src.infrastructure.persistence.database.Database",
        return_value=mock_db,
    ):
        db = container.database()

    assert db is mock_db


@pytest.mark.unit
def test_container_database_returns_cached_instance():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_db = MagicMock()
    with patch(
        "src.infrastructure.persistence.database.Database",
        return_value=mock_db,
    ):
        db1 = container.database()
        db2 = container.database()

    assert db1 is db2


# ---------------------------------------------------------------------------
# Container.jwt_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_jwt_service_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_svc = MagicMock()
    with patch("src.infrastructure.auth.jwt_service.JWTService", return_value=mock_svc):
        svc = container.jwt_service()

    assert svc is mock_svc


# ---------------------------------------------------------------------------
# Container.google_oauth
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_google_oauth_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_svc = MagicMock()
    with patch(
        "src.infrastructure.auth.google_oauth.GoogleOAuthService",
        return_value=mock_svc,
    ):
        svc = container.google_oauth()

    assert svc is mock_svc


# ---------------------------------------------------------------------------
# Container.microsoft_oauth
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_microsoft_oauth_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_svc = MagicMock()
    mock_module = MagicMock()
    mock_module.MicrosoftOAuthService = MagicMock(return_value=mock_svc)

    with patch.dict(
        "sys.modules", {"src.infrastructure.auth.microsoft_oauth": mock_module}
    ):
        svc = container.microsoft_oauth()

    assert svc is mock_svc


# ---------------------------------------------------------------------------
# Container.llm_adapter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_llm_adapter_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_adapter = MagicMock()
    with patch(
        "src.infrastructure.llm.factory.create_llm_adapter",
        return_value=mock_adapter,
    ):
        adapter = container.llm_adapter()

    assert adapter is mock_adapter


# ---------------------------------------------------------------------------
# Container.usage_tracker
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_usage_tracker_creates_with_cache():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_cache = MagicMock()
    mock_tracker = MagicMock()

    with (
        patch.object(container, "cache", return_value=mock_cache),
        patch(
            "src.billing.usage_tracker.RedisUsageTracker",
            return_value=mock_tracker,
        ),
    ):
        tracker = container.usage_tracker()

    assert tracker is mock_tracker


# ---------------------------------------------------------------------------
# Container.calendar_adapter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_calendar_adapter_creates_and_sets_db_factory():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_adapter = MagicMock()
    mock_db = MagicMock()
    mock_db.session_factory = MagicMock()

    with (
        patch(
            "src.infrastructure.calendar_providers.provider_aware_calendar.ProviderAwareCalendarAdapter",
            return_value=mock_adapter,
        ),
        patch.object(container, "database", return_value=mock_db),
    ):
        adapter = container.calendar_adapter()

    assert adapter is mock_adapter
    mock_adapter.set_db_session_factory.assert_called_once_with(mock_db.session_factory)


# ---------------------------------------------------------------------------
# Container.intent_router
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_intent_router_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_router = MagicMock()
    with patch("src.agent.router.IntentRouter", return_value=mock_router):
        router = container.intent_router()

    assert router is mock_router


# ---------------------------------------------------------------------------
# Container.calendar_agent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_calendar_agent_lazy_creates():
    from src.config.container import Container

    settings = _make_settings()
    container = Container(settings)

    mock_cal_agent = MagicMock()
    mock_cal_adapter = MagicMock()
    mock_cache = MagicMock()

    with (
        patch.object(container, "calendar_adapter", return_value=mock_cal_adapter),
        patch.object(container, "cache", return_value=mock_cache),
        patch("src.application.services.calendar_service.CalendarService"),
        patch(
            "src.agent.graph.CalendarAgentGraph",
            return_value=mock_cal_agent,
        ),
    ):
        agent = container.calendar_agent()

    assert agent is mock_cal_agent


# ---------------------------------------------------------------------------
# Container.cache — Redis path (production, redis_url set)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_container_cache_uses_redis_in_production():
    from src.config.container import Container

    settings = _make_settings(
        is_development=False, is_production=True, redis_url="redis://x:6379/0"
    )
    container = Container(settings)

    mock_redis_cache = MagicMock()
    with patch(
        "src.infrastructure.cache.redis_cache.RedisCacheAdapter",
        return_value=mock_redis_cache,
    ):
        cache = container.cache()

    assert cache is mock_redis_cache


@pytest.mark.unit
def test_container_cache_dev_mode_redis_unavailable_uses_in_memory():
    """In dev mode, if Redis is unavailable, falls back to InMemoryCacheAdapter."""
    from src.config.container import Container

    settings = _make_settings(is_development=True)
    container = Container(settings)

    mock_in_memory = MagicMock()

    # Mock asyncio.get_running_loop to raise RuntimeError (no running loop)
    # and asyncio.run to return False (redis down)
    with (
        patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        patch("asyncio.run", return_value=False),
        patch(
            "src.infrastructure.cache.in_memory_cache.InMemoryCacheAdapter",
            return_value=mock_in_memory,
        ),
    ):
        cache = container.cache()

    assert cache is mock_in_memory


@pytest.mark.unit
def test_container_cache_dev_mode_exception_uses_in_memory():
    """In dev mode, if cache init raises, falls back to InMemoryCacheAdapter."""
    from src.config.container import Container

    settings = _make_settings(is_development=True)
    container = Container(settings)

    mock_in_memory = MagicMock()

    with (
        patch("asyncio.get_running_loop", side_effect=Exception("unexpected")),
        patch(
            "src.infrastructure.cache.in_memory_cache.InMemoryCacheAdapter",
            return_value=mock_in_memory,
        ),
    ):
        cache = container.cache()

    assert cache is mock_in_memory
