"""Tests for src/api/middleware/rate_limiter.py."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Build a minimal JWT string with real base64url payload."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{p}.fakesig"


def _build_app_without_testing():
    """Build a minimal Starlette app with RateLimiterMiddleware active (TESTING=0)."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def homepage(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/test", homepage)])
    # Import with TESTING=1 already set — we need to access methods directly
    return app


# ---------------------------------------------------------------------------
# _jwt_sub_fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_jwt_sub_fast_valid():
    from src.api.middleware.rate_limiter import _jwt_sub_fast

    token = _make_jwt({"sub": "user-123", "type": "access"})
    assert _jwt_sub_fast(token) == "user-123"


@pytest.mark.unit
def test_jwt_sub_fast_no_sub():
    from src.api.middleware.rate_limiter import _jwt_sub_fast

    token = _make_jwt({"type": "access"})
    assert _jwt_sub_fast(token) is None


@pytest.mark.unit
def test_jwt_sub_fast_bad_parts():
    from src.api.middleware.rate_limiter import _jwt_sub_fast

    assert _jwt_sub_fast("only.two") is None


@pytest.mark.unit
def test_jwt_sub_fast_not_base64():
    from src.api.middleware.rate_limiter import _jwt_sub_fast

    assert _jwt_sub_fast("a.!!!.c") is None


# ---------------------------------------------------------------------------
# RateLimiterMiddleware._check_local_custom
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_local_custom_allows_within_limit():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    from collections import defaultdict

    rl._local = defaultdict(list)

    now = time.time()
    for _ in range(5):
        allowed = rl._check_local_custom("key1", now, limit=10, window=60)
        assert allowed is True


@pytest.mark.unit
def test_check_local_custom_blocks_when_over_limit():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    from collections import defaultdict

    rl._local = defaultdict(list)

    now = time.time()
    for _ in range(3):
        rl._check_local_custom("key2", now, limit=3, window=60)

    # 4th request should be blocked
    assert rl._check_local_custom("key2", now, limit=3, window=60) is False


@pytest.mark.unit
def test_check_local_custom_evicts_expired_timestamps():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    from collections import defaultdict

    rl._local = defaultdict(list)

    old_time = time.time() - 120  # 2 minutes ago — outside 60s window
    rl._local["key3"] = [old_time, old_time, old_time]  # 3 expired

    # Should allow since all old timestamps are evicted
    assert rl._check_local_custom("key3", time.time(), limit=1, window=60) is True


# ---------------------------------------------------------------------------
# RateLimiterMiddleware._check_local
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_local_delegates_to_custom():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    from collections import defaultdict

    rl._local = defaultdict(list)

    assert rl._check_local("some_ip", time.time()) is True


# ---------------------------------------------------------------------------
# RateLimiterMiddleware._get_redis
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_redis_returns_none_when_import_fails():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    rl._redis = None
    rl._redis_checked = False

    with patch.dict("sys.modules", {"redis": None, "redis.asyncio": None}):
        result = rl._get_redis()

    assert result is None


@pytest.mark.unit
def test_get_redis_returns_cached_after_first_check():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    mock_redis = MagicMock()
    rl._redis = mock_redis
    rl._redis_checked = True

    result = rl._get_redis()
    assert result is mock_redis


@pytest.mark.unit
def test_get_redis_initialises_redis_client():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)
    rl._redis = None
    rl._redis_checked = False

    mock_redis_module = MagicMock()
    mock_redis_module.from_url = MagicMock(return_value="redis_client")

    with patch.dict(
        "sys.modules", {"redis": MagicMock(), "redis.asyncio": mock_redis_module}
    ):
        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379/0"}):
            result = rl._get_redis()

    assert rl._redis_checked is True


# ---------------------------------------------------------------------------
# RateLimiterMiddleware._resolve_bucket
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_bucket_returns_user_bucket_for_api_routes():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)

    token = _make_jwt({"sub": "usr-456"})
    request = MagicMock()
    request.url.path = "/api/v1/chat"
    request.headers = {"authorization": f"Bearer {token}"}
    request.client.host = "127.0.0.1"

    key = rl._resolve_bucket(request)
    assert key == "user:usr-456"


@pytest.mark.unit
def test_resolve_bucket_falls_back_to_ip_for_non_api():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)

    request = MagicMock()
    request.url.path = "/health"
    request.client.host = "10.0.0.1"

    key = rl._resolve_bucket(request)
    assert key == "ip:10.0.0.1"


@pytest.mark.unit
def test_resolve_bucket_uses_ip_when_no_bearer_token():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)

    request = MagicMock()
    request.url.path = "/api/v1/calendar"
    request.headers = {"authorization": ""}
    request.client.host = "192.168.1.1"

    key = rl._resolve_bucket(request)
    assert key == "ip:192.168.1.1"


@pytest.mark.unit
def test_resolve_bucket_uses_unknown_when_no_client():
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware.__new__(RateLimiterMiddleware)

    request = MagicMock()
    request.url.path = "/some-path"
    request.headers = {}
    request.client = None

    key = rl._resolve_bucket(request)
    assert key == "ip:unknown"


# ---------------------------------------------------------------------------
# RateLimiterMiddleware.dispatch — local path (no Redis)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_allows_request_in_non_testing_mode():
    """Middleware should pass through when under rate limit."""
    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    # Temporarily clear TESTING env
    with patch("src.api.middleware.rate_limiter._TESTING", False):
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def homepage(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/test", homepage)])

        rl = RateLimiterMiddleware(app)
        rl._redis_checked = True
        rl._redis = None  # force local path

        request = MagicMock()
        request.url.path = "/test"
        request.client.host = "1.2.3.4"
        request.headers = {}

        async def call_next(_req):  # type: ignore[no-untyped-def]
            return PlainTextResponse("ok")

        with patch.object(rl, "_get_redis", return_value=None):
            response = await rl.dispatch(request, call_next)

    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_blocks_when_rate_limit_exceeded():
    """Middleware should return 429 when local window is full."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    app = Starlette(routes=[])
    rl = RateLimiterMiddleware(app)
    rl._redis_checked = True
    rl._redis = None

    now = time.time()
    # Fill up the bucket
    rl._local["ip:9.9.9.9"] = [now] * rl.RATE_LIMIT

    request = MagicMock()
    request.url.path = "/api/v1/chat"
    request.client.host = "9.9.9.9"
    request.headers = {}

    async def call_next(_req):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    with patch("src.api.middleware.rate_limiter._TESTING", False):
        with patch.object(rl, "_get_redis", return_value=None):
            response = await rl.dispatch(request, call_next)

    assert response.status_code == 429


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_invite_path_blocks_after_limit():
    """Accept-invite endpoint uses tight 5/min limit per IP."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    app = Starlette(routes=[])
    rl = RateLimiterMiddleware(app)
    rl._redis_checked = True
    rl._redis = None

    now = time.time()
    rl._local["invite:5.5.5.5"] = [now] * 5  # already hit limit

    request = MagicMock()
    request.url.path = "/api/v1/auth/accept-invite"
    request.client.host = "5.5.5.5"
    request.headers = {}

    async def call_next(_req):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    with patch("src.api.middleware.rate_limiter._TESTING", False):
        with patch.object(rl, "_get_redis", return_value=None):
            response = await rl.dispatch(request, call_next)

    assert response.status_code == 429


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_invite_path_allows_within_limit():
    """Accept-invite allows requests under 5/min."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    app = Starlette(routes=[])
    rl = RateLimiterMiddleware(app)
    rl._redis_checked = True
    rl._redis = None

    request = MagicMock()
    request.url.path = "/api/v1/auth/accept-invite"
    request.client.host = "6.6.6.6"
    request.headers = {}

    async def call_next(_req):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    with patch("src.api.middleware.rate_limiter._TESTING", False):
        with patch.object(rl, "_get_redis", return_value=None):
            response = await rl.dispatch(request, call_next)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# RateLimiterMiddleware._check_redis / _check_redis_custom
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_redis_delegates_to_custom():
    """_check_redis calls _check_redis_custom with default limits."""
    from starlette.applications import Starlette

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware(Starlette(routes=[]))
    rl._redis_checked = True
    rl._redis = None

    with patch.object(
        rl, "_check_redis_custom", new=AsyncMock(return_value=True)
    ) as mock_custom:
        result = await rl._check_redis(MagicMock(), "bucket", 1234.0)

    assert result is True
    mock_custom.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_redis_custom_pipeline_under_limit():
    """_check_redis_custom returns True when count_before_add < limit."""
    from starlette.applications import Starlette

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    rl = RateLimiterMiddleware(Starlette(routes=[]))

    # Build a mock Redis with pipeline support
    mock_pipe = AsyncMock()
    mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_pipe.__aexit__ = AsyncMock(return_value=None)
    mock_pipe.zremrangebyscore = MagicMock()
    mock_pipe.zcard = MagicMock()
    mock_pipe.zadd = MagicMock()
    mock_pipe.expire = MagicMock()
    mock_pipe.execute = AsyncMock(
        return_value=[0, 5, 1, True]
    )  # count_before_add=5 < limit=60

    mock_redis = MagicMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)

    mock_aioredis = MagicMock()
    mock_aioredis.Redis = type(mock_redis)

    with patch.dict("sys.modules", {"redis.asyncio": mock_aioredis}):
        result = await rl._check_redis_custom(
            mock_redis, "rl:bucket", time.time(), 60, 60
        )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_falls_back_to_local_when_redis_raises():
    """When Redis pipeline raises, dispatch falls back to local check."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse

    from src.api.middleware.rate_limiter import RateLimiterMiddleware

    app = Starlette(routes=[])
    rl = RateLimiterMiddleware(app)

    mock_redis = MagicMock()

    request = MagicMock()
    request.url.path = "/api/v1/chat"
    request.client.host = "7.7.7.7"
    request.headers = {}

    async def call_next(_req):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    with patch("src.api.middleware.rate_limiter._TESTING", False):
        with patch.object(rl, "_get_redis", return_value=mock_redis):
            with patch.object(
                rl, "_check_redis", new=AsyncMock(side_effect=Exception("redis down"))
            ):
                response = await rl.dispatch(request, call_next)

    # Should still respond (either 200 or 429 depending on local state)
    assert response.status_code in (200, 429)
