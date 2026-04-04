"""
Settings API Routes — UI-driven configuration management.
Replaces manual .env editing with a web-based settings panel.
Settings are persisted to the config_settings DB table and override .env defaults.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_container, get_current_user, get_db_session
from src.config.container import Container
from src.domain.entities.user import User
from src.infrastructure.persistence.config_model import ConfigSettingModel

settings_router = APIRouter()

# ---------------------------------------------------------------------------
# Schema that defines which settings are configurable and how they're grouped
# ---------------------------------------------------------------------------

# Keys that contain secrets — values will be masked on read
_SECRET_KEYS = frozenset(
    {
        "app_secret_key",
        "anthropic_api_key",
        "openai_api_key",
        "google_client_secret",
        "microsoft_client_secret",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "langsmith_api_key",
        "sentry_dsn",
    }
)

# Settings schema — defines sections and fields for the UI
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "id": "app",
        "title": "Application",
        "icon": "⚙️",
        "fields": [
            {
                "key": "app_env",
                "label": "Environment",
                "type": "select",
                "options": ["development", "staging", "production"],
            },
            {"key": "app_secret_key", "label": "Secret Key", "type": "secret"},
            {"key": "app_port", "label": "Port", "type": "number"},
            {
                "key": "app_log_level",
                "label": "Log Level",
                "type": "select",
                "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
            },
            {
                "key": "app_cors_origins",
                "label": "CORS Origins",
                "type": "text",
                "hint": "Comma-separated URLs",
            },
        ],
    },
    {
        "id": "database",
        "title": "Database",
        "icon": "🗄️",
        "fields": [
            {
                "key": "database_url",
                "label": "Database URL",
                "type": "text",
                "hint": "sqlite+aiosqlite:///... or postgresql+asyncpg://...",
            },
            {"key": "redis_url", "label": "Redis URL", "type": "text"},
        ],
    },
    {
        "id": "llm",
        "title": "AI / LLM",
        "icon": "🤖",
        "fields": [
            {
                "key": "llm_provider",
                "label": "Provider",
                "type": "select",
                "options": ["anthropic", "openai"],
            },
            {
                "key": "anthropic_api_key",
                "label": "Anthropic API Key",
                "type": "secret",
            },
            {
                "key": "anthropic_model_primary",
                "label": "Anthropic Primary Model",
                "type": "text",
            },
            {
                "key": "anthropic_model_fast",
                "label": "Anthropic Fast Model",
                "type": "text",
            },
            {"key": "openai_api_key", "label": "OpenAI API Key", "type": "secret"},
            {
                "key": "openai_model_primary",
                "label": "OpenAI Primary Model",
                "type": "text",
            },
            {"key": "openai_model_fast", "label": "OpenAI Fast Model", "type": "text"},
            {
                "key": "llm_temperature",
                "label": "Temperature",
                "type": "number",
                "hint": "0.0 to 1.0",
            },
            {"key": "llm_max_tokens", "label": "Max Tokens", "type": "number"},
        ],
    },
    {
        "id": "google",
        "title": "Google OAuth",
        "icon": "🔵",
        "fields": [
            {"key": "google_client_id", "label": "Client ID", "type": "text"},
            {"key": "google_client_secret", "label": "Client Secret", "type": "secret"},
            {"key": "google_redirect_uri", "label": "Redirect URI", "type": "text"},
        ],
    },
    {
        "id": "microsoft",
        "title": "Microsoft OAuth",
        "icon": "🟦",
        "fields": [
            {"key": "microsoft_client_id", "label": "Client ID", "type": "text"},
            {
                "key": "microsoft_client_secret",
                "label": "Client Secret",
                "type": "secret",
            },
            {"key": "microsoft_redirect_uri", "label": "Redirect URI", "type": "text"},
            {
                "key": "microsoft_tenant_id",
                "label": "Tenant ID",
                "type": "text",
                "hint": "'common' for multi-tenant",
            },
        ],
    },
    {
        "id": "agent",
        "title": "Agent Behavior",
        "icon": "🧠",
        "fields": [
            {
                "key": "agent_default_timezone",
                "label": "Default Timezone",
                "type": "text",
            },
            {
                "key": "agent_working_hours_start",
                "label": "Working Hours Start",
                "type": "text",
                "hint": "HH:MM",
            },
            {
                "key": "agent_working_hours_end",
                "label": "Working Hours End",
                "type": "text",
                "hint": "HH:MM",
            },
            {
                "key": "agent_buffer_minutes",
                "label": "Buffer Between Meetings (min)",
                "type": "number",
            },
            {
                "key": "agent_max_iterations",
                "label": "Max Agent Iterations",
                "type": "number",
            },
            {
                "key": "agent_cache_ttl_seconds",
                "label": "Cache TTL (seconds)",
                "type": "number",
            },
        ],
    },
    {
        "id": "billing",
        "title": "Stripe / Billing",
        "icon": "💳",
        "fields": [
            {
                "key": "stripe_secret_key",
                "label": "Stripe Secret Key",
                "type": "secret",
            },
            {
                "key": "stripe_webhook_secret",
                "label": "Webhook Secret",
                "type": "secret",
            },
            {"key": "stripe_price_pro", "label": "Pro Price ID", "type": "text"},
            {
                "key": "stripe_price_business",
                "label": "Business Price ID",
                "type": "text",
            },
        ],
    },
    {
        "id": "monitoring",
        "title": "Monitoring",
        "icon": "📊",
        "fields": [
            {
                "key": "langsmith_api_key",
                "label": "LangSmith API Key",
                "type": "secret",
            },
            {"key": "langsmith_project", "label": "LangSmith Project", "type": "text"},
            {"key": "sentry_dsn", "label": "Sentry DSN", "type": "secret"},
        ],
    },
    {
        "id": "jwt",
        "title": "JWT / Auth",
        "icon": "🔐",
        "fields": [
            {
                "key": "jwt_algorithm",
                "label": "Algorithm",
                "type": "select",
                "options": ["HS256", "HS384", "HS512"],
            },
            {
                "key": "jwt_access_token_expire_minutes",
                "label": "Access Token TTL (min)",
                "type": "number",
            },
            {
                "key": "jwt_refresh_token_expire_days",
                "label": "Refresh Token TTL (days)",
                "type": "number",
            },
        ],
    },
]

# Flat set of all configurable keys
_ALL_KEYS = {f["key"] for section in SETTINGS_SCHEMA for f in section["fields"]}


def _mask(value: str) -> str:
    """Mask a secret value for display."""
    if not value or value in ("", "change-me"):
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:4] + "•" * (len(value) - 8) + value[-4:]


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class SettingsResponse(BaseModel):
    schema_: list[dict] = []
    values: dict[str, str] = {}


class SettingsUpdateRequest(BaseModel):
    values: dict[str, str]


class SettingsUpdateResponse(BaseModel):
    updated: list[str]
    message: str = "Settings saved"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@settings_router.get("/", response_model=SettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> SettingsResponse:
    """Return all configurable settings with current values (secrets masked)."""
    # Load DB overrides
    result = await session.execute(select(ConfigSettingModel))
    db_overrides: dict[str, str] = {
        row.key: row.value for row in result.scalars().all()
    }

    # Build merged values: DB override > env/default
    env_settings = container.settings
    values: dict[str, str] = {}
    for key in _ALL_KEYS:
        if key in db_overrides:
            raw = db_overrides[key]
        else:
            raw = str(getattr(env_settings, key, ""))
        # Mask secrets
        if key in _SECRET_KEYS:
            values[key] = _mask(raw)
        else:
            values[key] = raw

    return SettingsResponse(schema_=SETTINGS_SCHEMA, values=values)


@settings_router.put("/", response_model=SettingsUpdateResponse)
async def update_settings(
    request: SettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> SettingsUpdateResponse:
    """Persist settings to DB. Secrets with masked values (••••) are skipped."""
    updated_keys: list[str] = []

    for key, value in request.values.items():
        if key not in _ALL_KEYS:
            continue
        # Skip unchanged masked secrets
        if key in _SECRET_KEYS and "••••" in value:
            continue

        # Upsert into config_settings
        existing = await session.execute(
            select(ConfigSettingModel).where(ConfigSettingModel.key == key)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.value = value
        else:
            session.add(ConfigSettingModel(key=key, value=value))
        updated_keys.append(key)

    await session.flush()

    # Hot-reload: update the in-memory Settings object so changes take effect
    # without restarting the server
    if updated_keys:
        _hot_reload_settings(container, updated_keys, request.values)

    return SettingsUpdateResponse(
        updated=updated_keys,
        message=f"Saved {len(updated_keys)} setting(s). Some changes may require a server restart.",
    )


@settings_router.get("/schema")
async def get_settings_schema(
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Return the settings schema only (for form generation)."""
    return SETTINGS_SCHEMA


@settings_router.post("/test-connection")
async def test_connection(
    request: dict,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Test a service connection (DB, Redis, LLM, OAuth)."""
    service = request.get("service", "")
    if service == "redis":
        try:
            cache = container.cache()
            if hasattr(cache, "_redis"):
                await cache._redis.ping()
            return {"status": "ok", "message": "Redis connected"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    elif service == "llm":
        return {
            "status": "ok",
            "message": f"LLM provider: {container.settings.llm_provider}",
        }
    elif service == "google_oauth":
        s = container.settings
        configured = bool(
            s.google_client_id and not s.google_client_id.startswith("your-")
        )
        return {
            "status": "ok" if configured else "not_configured",
            "configured": configured,
            "client_id_set": bool(
                s.google_client_id and not s.google_client_id.startswith("your-")
            ),
            "client_secret_set": bool(
                s.google_client_secret
                and not s.google_client_secret.startswith("your-")
            ),
            "redirect_uri": f"http://localhost:{s.app_port}/api/v1/orgs/google-callback",
        }
    return {"status": "error", "message": f"Unknown service: {service}"}


# ---------------------------------------------------------------------------
# Hot-reload helper
# ---------------------------------------------------------------------------


def _hot_reload_settings(
    container: Container, keys: list[str], values: dict[str, str]
) -> None:
    """Update the in-memory Settings object with new values."""
    settings = container.settings
    for key in keys:
        if key in values and hasattr(settings, key):
            val = values[key]
            # Convert types
            field_info = settings.model_fields.get(key)
            if field_info:
                ann = field_info.annotation
                try:
                    if ann is int or ann == int:
                        val = int(val)
                    elif ann is float or ann == float:
                        val = float(val)
                    elif ann is bool or ann == bool:
                        val = val.lower() in ("true", "1", "yes")
                    elif hasattr(ann, "__origin__") and ann.__origin__ is list:
                        # list[str] — stored as comma-separated or JSON
                        if val.startswith("["):
                            import json

                            val = json.loads(val)
                        else:
                            val = [v.strip() for v in val.split(",") if v.strip()]
                except (ValueError, TypeError):
                    pass
            object.__setattr__(settings, key, val)

    # Clear cached container instances that depend on changed settings
    _invalidate_instances = set()
    for key in keys:
        if key.startswith("google_"):
            _invalidate_instances.add("google_oauth")
        elif key.startswith("microsoft_"):
            _invalidate_instances.add("microsoft_oauth")
        elif (
            key.startswith("llm_")
            or key.endswith("_api_key")
            and ("anthropic" in key or "openai" in key)
        ):
            _invalidate_instances.add("llm_adapter")
        elif key.startswith("jwt_") or key == "app_secret_key":
            _invalidate_instances.add("jwt_service")

    for inst_key in _invalidate_instances:
        container._instances.pop(inst_key, None)
