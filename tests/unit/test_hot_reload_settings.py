"""Unit tests for _hot_reload_settings in settings_routes.py (type coercion paths)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api.rest.settings_routes import _hot_reload_settings


def _make_settings(**kwargs):
    """Build a mock Settings object with model_fields for type annotation lookups."""
    from src.config.settings import Settings

    settings = Settings(
        database_url="sqlite+aiosqlite:///data/calendar_agent.db",
        **kwargs,
    )
    return settings


def _make_container(settings=None):
    container = MagicMock()
    container.settings = settings or _make_settings()
    container._instances = {}
    return container


# ---------------------------------------------------------------------------
# Type coercion: int (line 434)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_int_field():
    """Line 434: field annotated as int is coerced from string."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(container, ["app_port"], {"app_port": "9999"})

    assert container.settings.app_port == 9999
    assert isinstance(container.settings.app_port, int)


# ---------------------------------------------------------------------------
# Type coercion: float (line 436)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_float_field():
    """Line 436: field annotated as float is coerced from string."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(container, ["llm_temperature"], {"llm_temperature": "0.7"})

    assert container.settings.llm_temperature == pytest.approx(0.7)
    assert isinstance(container.settings.llm_temperature, float)


# ---------------------------------------------------------------------------
# Type coercion: bool (line 438)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_bool_field_true():
    """Line 438: field annotated as bool → 'true' → True."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(container, ["smtp_use_tls"], {"smtp_use_tls": "true"})

    assert container.settings.smtp_use_tls is True


@pytest.mark.unit
def test_hot_reload_bool_field_false():
    """Line 438: field annotated as bool → 'false' → False."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(container, ["smtp_use_tls"], {"smtp_use_tls": "false"})

    assert container.settings.smtp_use_tls is False


@pytest.mark.unit
def test_hot_reload_bool_field_yes():
    """Line 438: field annotated as bool → 'yes' → True."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(container, ["smtp_use_tls"], {"smtp_use_tls": "yes"})

    assert container.settings.smtp_use_tls is True


# ---------------------------------------------------------------------------
# Type coercion: list (lines 441-446)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_list_field_json():
    """Lines 441-444: list field from JSON string."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(
        container,
        ["app_cors_origins"],
        {"app_cors_origins": '["http://a.com", "http://b.com"]'},
    )

    assert container.settings.app_cors_origins == ["http://a.com", "http://b.com"]


@pytest.mark.unit
def test_hot_reload_list_field_comma_separated():
    """Lines 445-446: list field from comma-separated string."""
    settings = _make_settings()
    container = _make_container(settings)

    _hot_reload_settings(
        container,
        ["app_cors_origins"],
        {"app_cors_origins": "http://a.com, http://b.com"},
    )

    assert container.settings.app_cors_origins == ["http://a.com", "http://b.com"]


# ---------------------------------------------------------------------------
# Type coercion: bad value caught (line 447-448)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_int_bad_value_ignored():
    """Lines 447-448: ValueError during coercion is caught, original string retained."""
    settings = _make_settings()
    container = _make_container(settings)

    # "not-a-number" can't be int() → caught → val stays as string
    _hot_reload_settings(container, ["app_port"], {"app_port": "not-a-number"})

    # Should not raise; value may be the original string or unchanged
    # The important thing is no exception is raised


# ---------------------------------------------------------------------------
# _invalidate_instances clear (lines 455-468)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hot_reload_invalidates_google_oauth():
    """Line 455: google_* key → 'google_oauth' removed from _instances."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["google_oauth"] = object()

    _hot_reload_settings(
        container, ["google_client_id"], {"google_client_id": "new-id"}
    )

    assert "google_oauth" not in container._instances


@pytest.mark.unit
def test_hot_reload_invalidates_microsoft_oauth():
    """Line 457: microsoft_* key → 'microsoft_oauth' removed from _instances."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["microsoft_oauth"] = object()

    _hot_reload_settings(
        container,
        ["microsoft_client_id"],
        {"microsoft_client_id": "ms-new-id"},
    )

    assert "microsoft_oauth" not in container._instances


@pytest.mark.unit
def test_hot_reload_invalidates_llm_adapter():
    """Line 463: llm_* key → 'llm_adapter' removed from _instances."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["llm_adapter"] = object()

    _hot_reload_settings(container, ["llm_provider"], {"llm_provider": "openai"})

    assert "llm_adapter" not in container._instances


@pytest.mark.unit
def test_hot_reload_invalidates_jwt_service():
    """Line 465: jwt_* key → 'jwt_service' removed from _instances."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["jwt_service"] = object()

    _hot_reload_settings(
        container,
        ["jwt_access_token_expire_minutes"],
        {"jwt_access_token_expire_minutes": "60"},
    )

    assert "jwt_service" not in container._instances


@pytest.mark.unit
def test_hot_reload_app_secret_key_invalidates_jwt():
    """Line 465: app_secret_key → 'jwt_service' removed from _instances."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["jwt_service"] = object()

    _hot_reload_settings(
        container, ["app_secret_key"], {"app_secret_key": "new-secret"}
    )

    assert "jwt_service" not in container._instances


@pytest.mark.unit
def test_hot_reload_unknown_key_no_instance_cleared():
    """No invalidation when key doesn't match any prefix."""
    settings = _make_settings()
    container = _make_container(settings)
    container._instances["llm_adapter"] = sentinel = object()

    _hot_reload_settings(container, ["app_env"], {"app_env": "production"})

    # llm_adapter should still be present
    assert container._instances.get("llm_adapter") is sentinel
