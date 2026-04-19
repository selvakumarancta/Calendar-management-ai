"""
Rate Limiter Middleware — per-tenant request throttling.
Uses Redis sliding-window in production, in-memory sliding-window in dev.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# When TESTING=1 the rate limiter is disabled entirely so integration tests
# never hit 429 due to cross-test state bleed.
_TESTING = os.environ.get("TESTING") == "1"


def _jwt_sub_fast(token: str) -> str | None:
    """Extract the ``sub`` claim from a JWT without verifying the signature.

    Used only to derive a rate-limit bucket key — the auth middleware already
    validates the signature before the request reaches any endpoint handler.
    Returns ``None`` on any parse error.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # JWT payload is base64url-encoded; add padding as required
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return str(payload["sub"]) if payload.get("sub") else None
    except Exception:
        return None


# Paths that bypass rate limiting
# - /health, /ready: infrastructure probes — no auth, must always respond
# - /api/v1/billing/webhook: Stripe sends bursts on retries; throttling breaks subscription state
# - /ws: WebSocket upgrade — hand-rolled async; rate limiting is done at the WS message level
_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/nginx-health",
        "/api/v1/billing/webhook",
        "/ws",
    }
)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter.

    Production:  uses Redis ZADD/ZCOUNT so limits are shared across all
                 app replicas and survive restarts.
    Development: falls back to an in-process dict (zero deps required).
    """

    RATE_LIMIT = 60  # max requests
    WINDOW_SECONDS = 60  # per rolling window

    # invite accept-link is unauthenticated — apply a tighter limit to prevent
    # token enumeration / brute-force even at the middleware level
    _INVITE_LIMIT = 5
    _INVITE_WINDOW = 60  # 5 attempts per minute per IP

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._local: dict[str, list[float]] = defaultdict(list)
        self._redis: object | None = None
        self._redis_checked = False

    def _get_redis(self) -> object | None:
        """Lazy-init Redis client; returns None if unavailable."""
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            import os

            import redis.asyncio as aioredis

            url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            self._redis = aioredis.from_url(url, decode_responses=True)
        except Exception:
            self._redis = None
        return self._redis

    async def dispatch(self, request: Request, call_next: object) -> object:  # type: ignore[override]
        if _TESTING or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)  # type: ignore[misc]

        # Special tight limit on the unauthenticated accept-invite endpoint
        # to prevent token enumeration (5 attempts/min per IP).
        if request.url.path == "/api/v1/auth/accept-invite":
            client_ip = request.client.host if request.client else "unknown"
            bucket_key = f"invite:{client_ip}"
            now = time.time()
            redis = self._get_redis()
            if redis is not None:
                try:
                    allowed = await self._check_redis_custom(
                        redis,
                        bucket_key,
                        now,
                        self._INVITE_LIMIT,
                        self._INVITE_WINDOW,
                    )
                except Exception:
                    allowed = self._check_local_custom(
                        bucket_key, now, self._INVITE_LIMIT, self._INVITE_WINDOW
                    )
            else:
                allowed = self._check_local_custom(
                    bucket_key, now, self._INVITE_LIMIT, self._INVITE_WINDOW
                )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"error": "Too many invite attempts. Try again later."},
                )

        # Key on authenticated user ID for API routes, IP elsewhere.
        bucket_key = self._resolve_bucket(request)
        now = time.time()

        redis = self._get_redis()
        allowed = True
        if redis is not None:
            try:
                allowed = await self._check_redis(redis, bucket_key, now)
            except Exception:
                # Redis unavailable — fall back to local counter for this request
                allowed = self._check_local(bucket_key, now)
        else:
            allowed = self._check_local(bucket_key, now)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": self.WINDOW_SECONDS,
                },
            )
        return await call_next(request)  # type: ignore[misc]

    def _resolve_bucket(self, request: Request) -> str:
        """Return a rate-limit bucket key.

        For authenticated ``/api/v1/`` requests: ``user:<jwt-sub>``
        For everything else: ``ip:<client_ip>``

        We only decode the JWT payload (middle segment) — we don't verify the
        signature here because the auth middleware has already done that.  We
        just need the *subject* to namespace the bucket.
        """
        path = request.url.path
        if path.startswith("/api/v1/"):
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
                sub = _jwt_sub_fast(token)
                if sub:
                    return f"user:{sub}"

        client_ip = request.client.host if request.client else "unknown"
        return f"ip:{client_ip}"

    async def _check_redis(self, redis: object, key: str, now: float) -> bool:
        """Sliding-window check using Redis sorted set."""
        return await self._check_redis_custom(
            redis, key, now, self.RATE_LIMIT, self.WINDOW_SECONDS
        )

    async def _check_redis_custom(
        self,
        redis: object,
        key: str,
        now: float,
        limit: int,
        window: int,
    ) -> bool:
        """Parameterised sliding-window check using Redis sorted set."""
        import redis.asyncio as aioredis

        r: aioredis.Redis = redis  # type: ignore[assignment]
        rkey = f"rl:{key}"
        window_start = now - window

        async with r.pipeline(transaction=True) as pipe:  # type: ignore[union-attr]
            pipe.zremrangebyscore(rkey, "-inf", window_start)
            pipe.zcard(rkey)
            pipe.zadd(rkey, {str(now): now})
            pipe.expire(rkey, window + 1)
            results = await pipe.execute()

        count_before_add = results[1]
        return int(count_before_add) < limit

    def _check_local(self, client_ip: str, now: float) -> bool:
        """In-process sliding-window fallback."""
        return self._check_local_custom(
            client_ip, now, self.RATE_LIMIT, self.WINDOW_SECONDS
        )

    def _check_local_custom(
        self, key: str, now: float, limit: int, window: int
    ) -> bool:
        """Parameterised in-process sliding-window."""
        window_start = now - window
        self._local[key] = [t for t in self._local[key] if t > window_start]
        if len(self._local[key]) >= limit:
            return False
        self._local[key].append(now)
        return True
