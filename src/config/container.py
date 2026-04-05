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

    def calendar_agent(self):  # type: ignore[no-untyped-def]
        """Lazy-init LangGraph calendar agent."""
        if "calendar_agent" not in self._instances:
            from src.agent.graph import CalendarAgentGraph
            from src.application.services.calendar_service import CalendarService

            cal_adapter = self.calendar_adapter()
            calendar_service = CalendarService(
                calendar_provider=cal_adapter,
                event_repository=cal_adapter,  # type: ignore[arg-type]
                cache=self.cache(),
            )
            self._instances["calendar_agent"] = CalendarAgentGraph(
                calendar_service=calendar_service,
                llm_provider=self._settings.llm_provider,
                llm_api_key=self._settings.active_api_key,
                default_model=self._settings.active_model_fast,
                max_iterations=self._settings.agent_max_iterations,
                working_hours_start=self._settings.agent_working_hours_start,
                working_hours_end=self._settings.agent_working_hours_end,
            )
        return self._instances["calendar_agent"]

    def email_classifier(self):  # type: ignore[no-untyped-def]
        """Lazy-init email classifier service."""
        if "email_classifier" not in self._instances:
            from src.application.services.email_classifier_service import (
                EmailClassifierService,
            )

            self._instances["email_classifier"] = EmailClassifierService(
                llm_adapter=self.llm_adapter(),
            )
        return self._instances["email_classifier"]

    def analytics_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init analytics service."""
        if "analytics_service" not in self._instances:
            from src.application.services.analytics_service import AnalyticsService

            db = self.database()
            self._instances["analytics_service"] = AnalyticsService(
                db_session_factory=db.session_factory,
            )
        return self._instances["analytics_service"]

    def draft_composer(self):  # type: ignore[no-untyped-def]
        """Lazy-init draft composer service."""
        if "draft_composer" not in self._instances:
            from src.application.services.draft_composer_service import (
                DraftComposerService,
            )

            db = self.database()
            self._instances["draft_composer"] = DraftComposerService(
                llm_adapter=self.llm_adapter(),
                calendar_adapter=self.calendar_adapter(),
                db_session_factory=db.session_factory,
                analytics_service=self.analytics_service(),
                user_timezone=self._settings.agent_default_timezone,
                working_hours_start=self._settings.agent_working_hours_start,
                working_hours_end=self._settings.agent_working_hours_end,
            )
        return self._instances["draft_composer"]

    def user_guides_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init user guides service."""
        if "user_guides_service" not in self._instances:
            from src.application.services.user_guides_service import UserGuidesService

            db = self.database()
            self._instances["user_guides_service"] = UserGuidesService(
                llm_adapter=self.llm_adapter(),
                db_session_factory=db.session_factory,
            )
        return self._instances["user_guides_service"]

    def onboarding_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init onboarding service."""
        if "onboarding_service" not in self._instances:
            from src.application.services.onboarding_service import OnboardingService

            db = self.database()
            self._instances["onboarding_service"] = OnboardingService(
                llm_adapter=self.llm_adapter(),
                calendar_adapter=self.calendar_adapter(),
                db_session_factory=db.session_factory,
                lookback_days=60,
            )
        return self._instances["onboarding_service"]

    def scheduling_link_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init scheduling link service."""
        if "scheduling_link_service" not in self._instances:
            from src.application.services.scheduling_link_service import (
                SchedulingLinkService,
            )

            db = self.database()
            self._instances["scheduling_link_service"] = SchedulingLinkService(
                calendar_adapter=self.calendar_adapter(),
                db_session_factory=db.session_factory,
                base_url=self._settings.app_base_url,
                link_expiry_days=self._settings.scheduling_link_expiry_days,
                analytics_service=self.analytics_service(),
            )
        return self._instances["scheduling_link_service"]

    def message_hook_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init message hook service."""
        if "message_hook_service" not in self._instances:
            from src.application.services.message_hook_service import (
                MessageHookService,
            )

            db = self.database()
            self._instances["message_hook_service"] = MessageHookService(
                llm_adapter=self.llm_adapter(),
                calendar_adapter=self.calendar_adapter(),
                db_session_factory=db.session_factory,
                auto_create_threshold=self._settings.autopilot_confidence_threshold,
            )
        return self._instances["message_hook_service"]

    def booking_page_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init booking page service (Calendly / Cal.com slot reading)."""
        if "booking_page_service" not in self._instances:
            from src.application.services.booking_page_service import BookingPageService

            self._instances["booking_page_service"] = BookingPageService(
                calendly_api_key=getattr(self._settings, "calendly_api_key", ""),
                calcom_api_key=getattr(self._settings, "calcom_api_key", ""),
            )
        return self._instances["booking_page_service"]

    def invite_verification_service(self):  # type: ignore[no-untyped-def]
        """Lazy-init invite verification service."""
        if "invite_verification_service" not in self._instances:
            from src.application.services.invite_verification_service import (
                InviteVerificationService,
            )

            db = self.database()
            self._instances["invite_verification_service"] = InviteVerificationService(
                llm_adapter=self.llm_adapter(),
                calendar_adapter=self.calendar_adapter(),
                db_session_factory=db.session_factory,
            )
        return self._instances["invite_verification_service"]

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
