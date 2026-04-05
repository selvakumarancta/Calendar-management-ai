"""
Application Settings — centralized configuration via Pydantic Settings.
All config is driven by environment variables (.env file).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: str = "development"
    app_secret_key: str = "change-me"
    app_port: int = 8000
    app_log_level: str = "INFO"
    app_cors_origins: list[str] = ["http://localhost:3000"]

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./data/calendar_agent.db"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM Provider ---
    llm_provider: str = "anthropic"  # anthropic | openai

    # --- Anthropic / Claude ---
    anthropic_api_key: str = ""
    anthropic_model_primary: str = "claude-sonnet-4-20250514"
    anthropic_model_fast: str = "claude-haiku-3-20250414"

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_model_primary: str = "gpt-4o"
    openai_model_fast: str = "gpt-4o-mini"

    # --- Shared LLM ---
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096

    # --- Google Calendar ---
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    # --- Microsoft / Outlook OAuth ---
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_redirect_uri: str = "http://localhost:8000/api/v1/auth/microsoft/callback"
    microsoft_tenant_id: str = "common"  # 'common' for multi-tenant

    # --- Agent ---
    agent_max_iterations: int = 10
    agent_recursion_limit: int = 25
    agent_default_timezone: str = "UTC"
    agent_working_hours_start: str = "09:00"
    agent_working_hours_end: str = "17:00"
    agent_buffer_minutes: int = 15
    agent_cache_ttl_seconds: int = 300

    # --- Email Intelligence ---
    email_scan_interval_minutes: int = 15
    email_scan_window_hours: int = 72
    email_scan_initial_hours: int = 72

    # --- Scheduling Links ---
    app_base_url: str = "http://localhost:8000"
    scheduling_link_expiry_days: int = 7

    # --- Autopilot ---
    autopilot_confidence_threshold: float = 0.85

    # --- Stripe ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_business: str = ""

    # --- Monitoring ---
    langsmith_api_key: str = ""
    langsmith_project: str = "calendar-agent"
    sentry_dsn: str = ""

    # --- JWT ---
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def active_api_key(self) -> str:
        """Return the API key for the active LLM provider."""
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key
        return self.openai_api_key

    @property
    def active_model_primary(self) -> str:
        """Primary (complex reasoning) model for active provider."""
        if self.llm_provider == "anthropic":
            return self.anthropic_model_primary
        return self.openai_model_primary

    @property
    def active_model_fast(self) -> str:
        """Fast (cheap) model for active provider."""
        if self.llm_provider == "anthropic":
            return self.anthropic_model_fast
        return self.openai_model_fast


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
