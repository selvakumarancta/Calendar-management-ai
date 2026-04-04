"""
Dependency Injection Container — wires all layers together.
Follows the Composition Root pattern (assembled at startup, not scattered).
"""

from __future__ import annotations

from src.config.settings import Settings


class _NullUserRepository:
    """Placeholder user repository until full session wiring."""

    async def get_by_id(self, user_id):  # type: ignore[no-untyped-def]
        return None

    async def get_by_email(self, email):  # type: ignore[no-untyped-def]
        return None

    async def create(self, user):  # type: ignore[no-untyped-def]
        return user

    async def update(self, user):  # type: ignore[no-untyped-def]
        return user


class Container:
    """
    Central DI container — creates and holds all service instances.
    This is the composition root where hexagonal layers are wired.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instances: dict[str, object] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def database(self):  # type: ignore[no-untyped-def]
        """Lazy-init database connection."""
        if "database" not in self._instances:
            from src.infrastructure.persistence.database import Database

            self._instances["database"] = Database(
                database_url=self._settings.database_url,
                echo=self._settings.is_development,
            )
        return self._instances["database"]

    def cache(self):  # type: ignore[no-untyped-def]
        """Lazy-init cache — Redis if available, else in-memory fallback."""
        if "cache" not in self._instances:
            if self._settings.redis_url and not self._settings.is_development:
                from src.infrastructure.cache.redis_cache import RedisCacheAdapter

                self._instances["cache"] = RedisCacheAdapter(
                    redis_url=self._settings.redis_url,
                )
            else:
                # Dev mode: try a real Redis ping, fall back to in-memory
                try:
                    import asyncio

                    import redis.asyncio as aioredis

                    async def _check() -> bool:
                        r = aioredis.from_url(self._settings.redis_url)
                        try:
                            await r.ping()
                            return True
                        except Exception:
                            return False
                        finally:
                            await r.aclose()

                    # Run the check (handles already-running loop)
                    try:
                        loop = asyncio.get_running_loop()
                        # Inside an async context we can't block; skip Redis
                        redis_ok = False
                    except RuntimeError:
                        redis_ok = asyncio.run(_check())

                    if redis_ok:
                        from src.infrastructure.cache.redis_cache import (
                            RedisCacheAdapter,
                        )

                        self._instances["cache"] = RedisCacheAdapter(
                            redis_url=self._settings.redis_url,
                        )
                    else:
                        from src.infrastructure.cache.in_memory_cache import (
                            InMemoryCacheAdapter,
                        )

                        self._instances["cache"] = InMemoryCacheAdapter()
                except Exception:
                    from src.infrastructure.cache.in_memory_cache import (
                        InMemoryCacheAdapter,
                    )

                    self._instances["cache"] = InMemoryCacheAdapter()
        return self._instances["cache"]

    def jwt_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init JWT service."""
        if "jwt_service" not in self._instances:
            from src.infrastructure.auth.jwt_service import JWTService

            self._instances["jwt_service"] = JWTService(
                secret_key=self._settings.app_secret_key,
                algorithm=self._settings.jwt_algorithm,
                access_token_expire_minutes=self._settings.jwt_access_token_expire_minutes,
                refresh_token_expire_days=self._settings.jwt_refresh_token_expire_days,
            )
        return self._instances["jwt_service"]

    def google_oauth(self):  # type: ignore[no-untyped-def]
        """Lazy-init Google OAuth service."""
        if "google_oauth" not in self._instances:
            from src.infrastructure.auth.google_oauth import GoogleOAuthService

            self._instances["google_oauth"] = GoogleOAuthService(
                client_id=self._settings.google_client_id,
                client_secret=self._settings.google_client_secret,
                redirect_uri=self._settings.google_redirect_uri,
            )
        return self._instances["google_oauth"]

    def microsoft_oauth(self):  # type: ignore[no-untyped-def]
        """Lazy-init Microsoft OAuth service."""
        if "microsoft_oauth" not in self._instances:
            from src.infrastructure.auth.microsoft_oauth import MicrosoftOAuthService

            self._instances["microsoft_oauth"] = MicrosoftOAuthService(
                client_id=self._settings.microsoft_client_id,
                client_secret=self._settings.microsoft_client_secret,
                redirect_uri=self._settings.microsoft_redirect_uri,
                tenant_id=self._settings.microsoft_tenant_id,
            )
        return self._instances["microsoft_oauth"]

    def llm_adapter(self):  # type: ignore[no-untyped-def]
        """Lazy-init LLM adapter via factory (supports anthropic, openai, etc.)."""
        if "llm_adapter" not in self._instances:
            from src.infrastructure.llm.factory import create_llm_adapter

            self._instances["llm_adapter"] = create_llm_adapter(
                provider=self._settings.llm_provider,
                api_key=self._settings.active_api_key,
                default_model=self._settings.active_model_fast,
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
            )
        return self._instances["llm_adapter"]

    def usage_tracker(self):  # type: ignore[no-untyped-def]
        """Lazy-init usage tracker."""
        if "usage_tracker" not in self._instances:
            from src.billing.usage_tracker import RedisUsageTracker

            self._instances["usage_tracker"] = RedisUsageTracker(
                cache=self.cache(),
            )
        return self._instances["usage_tracker"]

    def calendar_adapter(self):  # type: ignore[no-untyped-def]
        """Lazy-init provider-aware calendar adapter.
        Uses real Google Calendar API when OAuth tokens exist,
        falls back to in-memory for dev/demo."""
        if "calendar_adapter" not in self._instances:
            from src.infrastructure.calendar_providers.provider_aware_calendar import (
                ProviderAwareCalendarAdapter,
            )

            adapter = ProviderAwareCalendarAdapter(
                google_client_id=self._settings.google_client_id,
                google_client_secret=self._settings.google_client_secret,
            )
            # Give it access to the DB session factory for token lookups
            db = self.database()
            adapter.set_db_session_factory(db.session_factory)
            self._instances["calendar_adapter"] = adapter
        return self._instances["calendar_adapter"]

    def intent_router(self):  # type: ignore[no-untyped-def]
        """Lazy-init intent router."""
        if "intent_router" not in self._instances:
            from src.agent.router import IntentRouter

            self._instances["intent_router"] = IntentRouter()
        return self._instances["intent_router"]

    async def shutdown(self) -> None:
        """Clean up all resources."""
        db = self._instances.get("database")
        if db and hasattr(db, "close"):
            await db.close()

        cache = self._instances.get("cache")
        if cache and hasattr(cache, "close"):
            await cache.close()

        llm = self._instances.get("llm_adapter")
        if llm and hasattr(llm, "close"):
            await llm.close()
