"""
Unit tests for CorrelationIdMiddleware and AuditLogService.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware.correlation_id import (
    HEADER_NAME,
    CorrelationIdMiddleware,
    client_ip_var,
    request_id_var,
)
from src.application.services.audit_log_service import AuditLogService

# ---------------------------------------------------------------------------
# CorrelationIdMiddleware
# ---------------------------------------------------------------------------


def _make_app(capture: dict) -> Starlette:
    """Build a minimal Starlette app with the middleware installed."""

    async def homepage(request: Request) -> Response:
        capture["req_id"] = request_id_var.get("")
        capture["client_ip"] = client_ip_var.get("")
        return Response("ok", status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(CorrelationIdMiddleware)
    return app


class TestCorrelationIdMiddleware:
    @pytest.mark.unit
    def test_injects_request_id_header_in_response(self):
        capture: dict = {}
        client = TestClient(_make_app(capture))
        resp = client.get("/")
        assert resp.status_code == 200
        assert HEADER_NAME in resp.headers
        # Should be a valid UUID
        uuid.UUID(resp.headers[HEADER_NAME])

    @pytest.mark.unit
    def test_passes_through_client_provided_request_id(self):
        capture: dict = {}
        client = TestClient(_make_app(capture))
        custom_id = str(uuid.uuid4())
        resp = client.get("/", headers={HEADER_NAME: custom_id})
        assert resp.headers[HEADER_NAME] == custom_id

    @pytest.mark.unit
    def test_request_id_set_in_contextvar(self):
        capture: dict = {}
        client = TestClient(_make_app(capture))
        custom_id = str(uuid.uuid4())
        client.get("/", headers={HEADER_NAME: custom_id})
        assert capture["req_id"] == custom_id

    @pytest.mark.unit
    def test_generates_uuid_when_no_header(self):
        capture: dict = {}
        client = TestClient(_make_app(capture))
        client.get("/")
        assert capture["req_id"]
        uuid.UUID(capture["req_id"])

    @pytest.mark.unit
    def test_client_ip_captured(self):
        capture: dict = {}
        client = TestClient(_make_app(capture))
        client.get("/")
        # TestClient uses 127.0.0.1 or empty string — just check type
        assert isinstance(capture["client_ip"], str)


# ---------------------------------------------------------------------------
# AuditLogService
# ---------------------------------------------------------------------------


def _make_audit_service() -> tuple[AuditLogService, AsyncMock]:
    """Return an AuditLogService backed by a mock session factory."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    @asynccontextmanager
    async def session_factory():
        yield mock_session

    service = AuditLogService(session_factory)
    return service, mock_session


class TestAuditLogService:
    @pytest.mark.unit
    async def test_record_commits_log_entry(self):
        service, session = _make_audit_service()
        await service.record("user.login", actor_id=uuid.uuid4())
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_adds_model_to_session(self):
        service, session = _make_audit_service()
        await service.record("user.delete", actor_id=uuid.uuid4())
        session.add.assert_called_once()

    @pytest.mark.unit
    async def test_record_with_all_fields(self):
        service, session = _make_audit_service()
        actor = uuid.uuid4()
        target = uuid.uuid4()
        await service.record(
            "org.member.invite",
            actor_id=actor,
            target_id=target,
            detail="invited user",
            metadata={"role": "admin"},
            request_id="req-123",
            ip_address="10.0.0.1",
        )
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_does_not_raise_on_db_failure(self):
        """Audit failures must never propagate."""
        error_session = AsyncMock()
        error_session.add = MagicMock()
        error_session.commit = AsyncMock(side_effect=RuntimeError("DB down"))

        @asynccontextmanager
        async def bad_factory():
            yield error_session

        service = AuditLogService(bad_factory)
        # Should not raise
        await service.record("test.action")

    @pytest.mark.unit
    async def test_record_with_string_uuids(self):
        service, session = _make_audit_service()
        # String UUIDs should be coerced without error
        await service.record(
            "test.action",
            actor_id=str(uuid.uuid4()),  # type: ignore[arg-type]
            target_id=str(uuid.uuid4()),  # type: ignore[arg-type]
        )
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_with_none_ids(self):
        service, session = _make_audit_service()
        await service.record("test.action", actor_id=None, target_id=None)
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_reads_request_id_from_contextvar(self):
        """When request_id is not passed, fallback to contextvar."""
        service, session = _make_audit_service()
        token = request_id_var.set("ctx-req-id")
        try:
            await service.record("test.action")
        finally:
            request_id_var.reset(token)
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_reads_ip_from_contextvar(self):
        service, session = _make_audit_service()
        token = client_ip_var.set("192.168.1.1")
        try:
            await service.record("test.action")
        finally:
            client_ip_var.reset(token)
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_with_invalid_uuid_strings_handled(self):
        """Invalid UUID string → coercion falls back to None, no exception."""
        service, session = _make_audit_service()
        await service.record(
            "test.action",
            actor_id="not-a-valid-uuid-string",  # type: ignore[arg-type]
            target_id="also-not-valid",  # type: ignore[arg-type]
        )
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_import_exception_caught_for_request_id(self):
        """Lines 50-51: exception during correlation_id import is swallowed."""
        service, session = _make_audit_service()
        import unittest.mock as _mock

        # Simulate import failure by making the module raise on import
        with _mock.patch.dict(
            "sys.modules", {"src.api.middleware.correlation_id": None}
        ):
            await service.record("test.action")
        session.commit.assert_awaited_once()

    @pytest.mark.unit
    async def test_record_import_exception_caught_for_ip(self):
        """Lines 59-60: exception during client_ip_var import is swallowed."""
        service, session = _make_audit_service()
        import sys
        import unittest.mock as _mock

        # Remove correlation_id from cache to force re-import inside record()
        broken = _mock.MagicMock(side_effect=ImportError("forced"))
        with _mock.patch.dict(
            "sys.modules", {"src.api.middleware.correlation_id": None}
        ):
            await service.record("test.action", request_id="given-req-id")
        session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Domain exceptions — uncovered subclasses
# ---------------------------------------------------------------------------

from src.domain.exceptions import (  # noqa: E402
    AgentError,
    AgentMaxIterationsError,
    CalendarProviderError,
    InsufficientPermissionsError,
    InvalidPlanError,
    QuotaExceededError,
    TokenExpiredError,
)


class TestDomainExceptionSubclasses:
    @pytest.mark.unit
    def test_token_expired_error_message(self):
        err = TokenExpiredError()
        assert "expired" in str(err).lower()

    @pytest.mark.unit
    def test_insufficient_permissions_includes_action(self):
        err = InsufficientPermissionsError("delete_user")
        assert "delete_user" in str(err)

    @pytest.mark.unit
    def test_insufficient_permissions_default(self):
        err = InsufficientPermissionsError()
        assert isinstance(err, Exception)

    @pytest.mark.unit
    def test_quota_exceeded_error_includes_plan(self):
        err = QuotaExceededError(plan="free", limit=100)
        assert "free" in str(err)
        assert "100" in str(err)

    @pytest.mark.unit
    def test_invalid_plan_error_includes_name(self):
        err = InvalidPlanError(plan="platinum")
        assert "platinum" in str(err)

    @pytest.mark.unit
    def test_agent_error_default_message(self):
        err = AgentError()
        assert "Agent" in str(err)

    @pytest.mark.unit
    def test_agent_max_iterations_error(self):
        err = AgentMaxIterationsError(max_iterations=25)
        assert "25" in str(err)

    @pytest.mark.unit
    def test_calendar_provider_error(self):
        err = CalendarProviderError(provider="Google", message="rate limited")
        assert "Google" in str(err)
        assert "rate limited" in str(err)


# ---------------------------------------------------------------------------
# Conversation entity — uncovered methods
# ---------------------------------------------------------------------------

from src.domain.entities.conversation import (  # noqa: E402
    Conversation,
    Message,
    MessageRole,
)


class TestConversationEntity:
    @pytest.mark.unit
    def test_add_message_appends_and_returns(self):
        conv = Conversation()
        msg = conv.add_message(MessageRole.USER, "hello")
        assert isinstance(msg, Message)
        assert len(conv.messages) == 1

    @pytest.mark.unit
    def test_get_active_window_returns_last_n(self):
        conv = Conversation()
        for i in range(15):
            conv.add_message(MessageRole.USER, f"msg {i}")
        window = conv.get_active_window()
        assert len(window) == 10
        assert window[-1].content == "msg 14"

    @pytest.mark.unit
    def test_get_total_tokens(self):
        conv = Conversation()
        conv.add_message(MessageRole.USER, "hi")
        conv.messages[0].token_count = 5
        conv.add_message(MessageRole.ASSISTANT, "hello")
        conv.messages[1].token_count = 7
        assert conv.get_total_tokens() == 12

    @pytest.mark.unit
    def test_message_count_property(self):
        conv = Conversation()
        assert conv.message_count == 0
        conv.add_message(MessageRole.SYSTEM, "prompt")
        assert conv.message_count == 1

    @pytest.mark.unit
    def test_add_message_updates_updated_at(self):
        import time

        conv = Conversation()
        before = conv.updated_at
        time.sleep(0.01)
        conv.add_message(MessageRole.USER, "test")
        assert conv.updated_at >= before


# ---------------------------------------------------------------------------
# ProviderConnection entity — uncovered methods
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from src.domain.entities.organization import (  # noqa: E402
    ConnectionStatus,
    ProviderConnection,
    ProviderType,
)


class TestProviderConnectionEntity:
    @pytest.mark.unit
    def test_is_token_valid_when_valid(self):
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        conn = ProviderConnection(
            access_token="tok",
            token_expiry=expiry,
        )
        assert conn.is_token_valid() is True

    @pytest.mark.unit
    def test_is_token_valid_when_expired(self):
        expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        conn = ProviderConnection(
            access_token="tok",
            token_expiry=expiry,
        )
        assert conn.is_token_valid() is False

    @pytest.mark.unit
    def test_is_token_valid_when_no_token(self):
        conn = ProviderConnection(access_token="")
        assert conn.is_token_valid() is False

    @pytest.mark.unit
    def test_is_token_valid_when_no_expiry(self):
        conn = ProviderConnection(access_token="tok", token_expiry=None)
        assert conn.is_token_valid() is False

    @pytest.mark.unit
    def test_refresh_tokens_updates_fields(self):
        conn = ProviderConnection(access_token="old", status=ConnectionStatus.EXPIRED)
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        conn.refresh_tokens("new_tok", "new_refresh", expiry)
        assert conn.access_token == "new_tok"
        assert conn.refresh_token == "new_refresh"
        assert conn.token_expiry == expiry
        assert conn.status == ConnectionStatus.ACTIVE

    @pytest.mark.unit
    def test_refresh_tokens_keeps_old_refresh_when_none(self):
        conn = ProviderConnection(access_token="old", refresh_token="keep_me")
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        conn.refresh_tokens("new_tok", None, expiry)
        assert conn.refresh_token == "keep_me"


# ---------------------------------------------------------------------------
# Settings — anthropic branch coverage
# ---------------------------------------------------------------------------

from src.config.settings import Settings  # noqa: E402


class TestSettingsAnthropicBranch:
    @pytest.mark.unit
    def test_active_api_key_anthropic(self):
        s = Settings(llm_provider="anthropic", anthropic_api_key="ant-key")
        assert s.active_api_key == "ant-key"

    @pytest.mark.unit
    def test_active_api_key_openai(self):
        s = Settings(llm_provider="openai", openai_api_key="oai-key")
        assert s.active_api_key == "oai-key"

    @pytest.mark.unit
    def test_active_model_primary_anthropic(self):
        s = Settings(llm_provider="anthropic", anthropic_model_primary="claude-3-opus")
        assert s.active_model_primary == "claude-3-opus"

    @pytest.mark.unit
    def test_active_model_primary_openai(self):
        s = Settings(llm_provider="openai", openai_model_primary="gpt-4o")
        assert s.active_model_primary == "gpt-4o"

    @pytest.mark.unit
    def test_active_model_fast_anthropic(self):
        s = Settings(llm_provider="anthropic", anthropic_model_fast="claude-3-haiku")
        assert s.active_model_fast == "claude-3-haiku"

    @pytest.mark.unit
    def test_active_model_fast_openai(self):
        s = Settings(llm_provider="openai", openai_model_fast="gpt-4o-mini")
        assert s.active_model_fast == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Logging config — production branch
# ---------------------------------------------------------------------------

from src.config.logging_config import configure_logging  # noqa: E402


class TestLoggingConfig:
    @pytest.mark.unit
    def test_configure_logging_development(self):
        """Should not raise for development env."""
        configure_logging(app_env="development", log_level="DEBUG")

    @pytest.mark.unit
    def test_configure_logging_production(self):
        """Should not raise for production env (JSON renderer branch)."""
        configure_logging(app_env="production", log_level="WARNING")


# ---------------------------------------------------------------------------
# EmailProvider abstract class — default implementations
# ---------------------------------------------------------------------------

import abc  # noqa: E402
from uuid import UUID as _UUID  # noqa: E402

from src.domain.interfaces.email_provider import EmailProviderPort  # noqa: E402


class _MinimalEmailProvider(EmailProviderPort):
    """Minimal concrete subclass to test default method implementations."""

    async def list_recent_emails(self, user_id, since, max_results=50, query=""):
        return []

    async def get_email(self, user_id, message_id):
        return None  # type: ignore[return-value]

    async def get_thread_messages(self, user_id, thread_id, user_email=""):
        return []

    async def create_draft_reply(
        self, user_id, thread_id, to, subject, body, cc="", content_type="plain"
    ):
        return ""

    async def send_draft(self, user_id, draft_provider_id):
        return ""

    async def mark_processed(self, user_id, message_id):
        return False


class TestEmailProviderDefaults:
    @pytest.mark.unit
    async def test_setup_pubsub_watch_returns_empty_dict(self):
        provider = _MinimalEmailProvider()
        result = await provider.setup_pubsub_watch(
            user_id=uuid.uuid4(), pubsub_topic="projects/x/topics/y"
        )
        assert result == {}

    @pytest.mark.unit
    async def test_stop_pubsub_watch_returns_true(self):
        provider = _MinimalEmailProvider()
        result = await provider.stop_pubsub_watch(user_id=uuid.uuid4())
        assert result is True
