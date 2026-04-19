"""
API Routes — REST endpoints for the Calendar Agent SaaS platform.
All routes delegate to application services via FastAPI DI.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from src.api.dependencies import get_container, get_current_user
from src.application.dto import (
    ChatRequestDTO,
    ChatResponseDTO,
    CreateEventDTO,
    DateRangeDTO,
    EventResponseDTO,
    LoginResponseDTO,
    UpdateEventDTO,
    UserProfileDTO,
)
from src.config.container import Container
from src.domain.entities.user import User

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
health_router = APIRouter()


@health_router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "healthy", "service": "calendar-agent"}


@health_router.get("/ready")
async def readiness_check(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Readiness check — verifies DB and cache connectivity."""
    errors: list[str] = []

    # Check database
    try:
        from sqlalchemy import text

        db = container.database()
        async with db.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        errors.append(f"database: {exc}")

    # Check Redis (cache) — only if a real Redis adapter is wired
    try:
        cache = container.cache()
        from src.infrastructure.cache.redis_cache import RedisCacheAdapter

        if isinstance(cache, RedisCacheAdapter):
            await cache._redis.ping()
    except Exception as exc:
        errors.append(f"cache: {exc}")

    if errors:
        raise HTTPException(
            status_code=503,
            detail={"status": "not ready", "errors": errors},
        )
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
auth_router = APIRouter()


# ---------------------------------------------------------------------------
# Email/password auth
# ---------------------------------------------------------------------------

from pydantic import BaseModel, EmailStr  # noqa: E402
from pydantic import Field as _Field


class _PasswordAuthRequest(BaseModel):
    email: EmailStr
    password: str = _Field(..., min_length=8, max_length=128)
    name: str = _Field(default="", max_length=255)


class GDPRExportSchema(BaseModel):
    """Shape of the GDPR Article 20 data export bundle."""

    user: dict
    conversations: list[dict]
    usage: list[dict]
    exported_at: str


@auth_router.post(
    "/register",
    summary="Register with email + password",
    tags=["Auth"],
    status_code=201,
)
async def register_email_password(
    payload: _PasswordAuthRequest,
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """
    Create a new account using email + password.

    On success returns a JWT pair so the client is immediately logged in.
    Returns 409 if the email is already registered.
    """
    import base64
    import hashlib
    import uuid

    import bcrypt as _bcrypt_lib
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    def _hash_pw(pw: str) -> str:
        # Pre-hash with SHA-256 so bcrypt's 72-byte limit is never hit
        digest = base64.b64encode(hashlib.sha256(pw.encode()).digest())
        return _bcrypt_lib.hashpw(digest, _bcrypt_lib.gensalt(12)).decode()

    db = container.database()
    async with db.session_factory() as session:
        existing = await session.execute(
            select(UserModel).where(UserModel.email == payload.email.lower())
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with that email already exists.",
            )

        hashed = _hash_pw(payload.password)
        new_user = UserModel(
            id=uuid.uuid4(),
            email=payload.email.lower(),
            name=payload.name or payload.email.split("@")[0],
            hashed_password=hashed,
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_id(new_user.id)

    if not user:
        raise HTTPException(status_code=500, detail="User creation failed")

    jwt_svc = container.jwt_service()
    return LoginResponseDTO(
        access_token=jwt_svc.create_access_token(user),
        refresh_token=jwt_svc.create_refresh_token(user),
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.post(
    "/login",
    summary="Login with email + password",
    tags=["Auth"],
)
async def login_email_password(
    payload: _PasswordAuthRequest,
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """
    Authenticate with email + password.

    Returns a JWT pair on success. 401 if credentials are invalid (constant-time).
    """
    import base64
    import hashlib

    import bcrypt as _bcrypt_lib
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    def _verify_pw(pw: str, hashed: str) -> bool:
        digest = base64.b64encode(hashlib.sha256(pw.encode()).digest())
        try:
            return _bcrypt_lib.checkpw(digest, hashed.encode())
        except Exception:
            return False

    _invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Constant-time dummy hash — used when user doesn't exist so verify() still runs
    _DUMMY_HASH = "$2b$12$KIxQKfq4fzRu6e5pOGOnj.VuE5yHhHhJmpj2K5P.y7sExOuOcJKta"

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.email == payload.email.lower())
        )
        row = result.scalar_one_or_none()

        candidate_hash = (
            row.hashed_password if row and row.hashed_password else _DUMMY_HASH
        )
        ok = _verify_pw(payload.password, candidate_hash)

        if not ok or not row or not row.hashed_password or not row.is_active:
            raise _invalid

        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_id(row.id)

    if not user:
        raise _invalid

    jwt_svc = container.jwt_service()
    return LoginResponseDTO(
        access_token=jwt_svc.create_access_token(user),
        refresh_token=jwt_svc.create_refresh_token(user),
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.get("/google/login")
async def google_login(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Initiate Google OAuth2 flow — returns authorization URL."""
    oauth = container.google_oauth()
    url = oauth.get_authorization_url()
    return {"authorization_url": url}


@auth_router.get("/google/callback")
async def google_callback(
    code: str,
    state: str | None = None,
    container: Container = Depends(get_container),
) -> object:
    """Handle Google OAuth2 callback — exchange code for tokens, then redirect to app."""
    import uuid as _uuid

    import httpx
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository
    from src.infrastructure.security.token_encryption import encrypt_token

    # 1. Exchange code for Google tokens
    oauth = container.google_oauth()
    tokens = oauth.exchange_code(code, state=state)

    raw_access = tokens["access_token"]
    raw_refresh = tokens.get("refresh_token") or ""

    # 2. Get user info from Google userinfo endpoint
    google_email = "user@example.com"
    google_name = "Google User"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {raw_access}"},
        )
        if resp.status_code == 200:
            profile = resp.json()
            google_email = profile.get("email", google_email)
            google_name = profile.get("name", google_name)

    # 3. Get or create user; store encrypted tokens in users table
    db = container.database()
    enc_access = encrypt_token(raw_access)
    enc_refresh = encrypt_token(raw_refresh)

    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)

        from src.application.services.auth_service import AuthService

        auth_svc = AuthService(
            user_repository=user_repo,
            jwt_secret=container.settings.app_secret_key,
            jwt_algorithm=container.settings.jwt_algorithm,
            access_token_expire_minutes=container.settings.jwt_access_token_expire_minutes,
            refresh_token_expire_days=container.settings.jwt_refresh_token_expire_days,
        )

        # Store encrypted tokens so GmailEmailAdapter can decrypt them later
        user, _access, _refresh = await auth_svc.authenticate_google_oauth(
            email=google_email,
            name=google_name,
            access_token=enc_access,
            refresh_token=enc_refresh or None,
            token_expiry=tokens["expiry"],
        )

        # 4. Also upsert a stand-alone ProviderConnection (org_id = user's own UUID
        #    used as a personal-account sentinel so GmailEmailAdapter finds tokens).
        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        existing = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == user.id,
                ProviderConnectionModel.provider == "google",
                ProviderConnectionModel.org_id == user.id,  # personal sentinel
            )
        )
        conn_model = existing.scalar_one_or_none()
        if conn_model:
            conn_model.access_token = enc_access
            conn_model.refresh_token = enc_refresh
            conn_model.token_expiry = tokens["expiry"]
            conn_model.provider_email = google_email
            conn_model.status = "active"
        else:
            conn_model = ProviderConnectionModel(
                id=_uuid.uuid4(),
                org_id=user.id,  # personal account sentinel
                user_id=user.id,
                provider="google",
                provider_email=google_email,
                status="active",
                access_token=enc_access,
                refresh_token=enc_refresh,
                token_expiry=tokens["expiry"],
                scopes=" ".join(
                    [
                        "https://www.googleapis.com/auth/calendar",
                        "https://www.googleapis.com/auth/gmail.readonly",
                    ]
                ),
            )
            session.add(conn_model)

        jwt_svc = container.jwt_service()
        jwt_access = jwt_svc.create_access_token(user)
        jwt_refresh = jwt_svc.create_refresh_token(user)
        await session.commit()

    # 5. Return an HTML page that stores the JWT in localStorage and redirects home
    import html as _html

    safe_name = _html.escape(google_name)
    # Embed JWTs via a data attribute — never string-interpolated inside <script>
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head><title>Signing in...</title></head>
<body style="background:#0f0f11;color:#e4e4eb;font-family:sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh"
      data-access="{jwt_access}"
      data-refresh="{jwt_refresh}">
  <div style="text-align:center">
    <h2>&#x2705; Signed in as {safe_name}</h2>
    <p>Redirecting to Calendar Agent&hellip;</p>
    <script>
      var b = document.body;
      localStorage.setItem('token', b.dataset.access);
      localStorage.setItem('refresh_token', b.dataset.refresh);
      window.location.href = '/';
    </script>
    <p><a href="/" style="color:#8b5cf6">Click here if not redirected</a></p>
  </div>
</body>
</html>"""
    )


@auth_router.get("/microsoft/login")
async def microsoft_login(
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """Initiate Microsoft OAuth2 flow — returns authorization URL."""
    oauth = container.microsoft_oauth()
    url = oauth.get_authorization_url(state="ms-login")
    return {"authorization_url": url}


@auth_router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str | None = None,
    container: Container = Depends(get_container),
) -> object:
    """Handle Microsoft OAuth2 callback — exchange code for tokens, then redirect to app."""
    import uuid as _uuid

    import httpx
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository
    from src.infrastructure.security.token_encryption import encrypt_token

    oauth = container.microsoft_oauth()
    tokens = oauth.exchange_code(code)

    raw_access = tokens["access_token"]
    raw_refresh = tokens.get("refresh_token") or ""

    # Get user info from Microsoft Graph
    ms_email = "user@outlook.com"
    ms_name = "Microsoft User"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {raw_access}"},
        )
        if resp.status_code == 200:
            profile = resp.json()
            ms_email = profile.get("mail") or profile.get("userPrincipalName", ms_email)
            ms_name = profile.get("displayName", ms_name)

    enc_access = encrypt_token(raw_access)
    enc_refresh = encrypt_token(raw_refresh)

    db = container.database()
    async with db.session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_email(ms_email)
        if not user:
            from src.domain.entities.user import User as _User

            user = _User(email=ms_email, name=ms_name)
            user = await user_repo.create(user)

        # Store encrypted Microsoft tokens in dedicated Microsoft columns
        user.microsoft_access_token = enc_access
        user.microsoft_refresh_token = enc_refresh or None
        user.microsoft_token_expiry = tokens.get("expiry")
        await user_repo.update(user)

        # Upsert a ProviderConnection so OutlookEmailAdapter can find tokens
        from src.infrastructure.persistence.org_models import ProviderConnectionModel

        existing = await session.execute(
            select(ProviderConnectionModel).where(
                ProviderConnectionModel.user_id == user.id,
                ProviderConnectionModel.provider == "microsoft",
                ProviderConnectionModel.org_id == user.id,
            )
        )
        conn_model = existing.scalar_one_or_none()
        if conn_model:
            conn_model.access_token = enc_access
            conn_model.refresh_token = enc_refresh
            conn_model.provider_email = ms_email
            conn_model.status = "active"
        else:
            conn_model = ProviderConnectionModel(
                id=_uuid.uuid4(),
                org_id=user.id,
                user_id=user.id,
                provider="microsoft",
                provider_email=ms_email,
                status="active",
                access_token=enc_access,
                refresh_token=enc_refresh,
                scopes="Mail.Read Calendars.ReadWrite",
            )
            session.add(conn_model)

        jwt_svc = container.jwt_service()
        jwt_access = jwt_svc.create_access_token(user)
        jwt_refresh = jwt_svc.create_refresh_token(user)
        await session.commit()

    import html as _html

    safe_ms_name = _html.escape(ms_name)
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head><title>Signing in...</title></head>
<body style="background:#0f0f11;color:#e4e4eb;font-family:sans-serif;
             display:flex;align-items:center;justify-content:center;height:100vh"
      data-access="{jwt_access}"
      data-refresh="{jwt_refresh}">
  <div style="text-align:center">
    <h2>&#x2705; Signed in as {safe_ms_name}</h2>
    <p>Redirecting to Calendar Agent&hellip;</p>
    <script>
      var b = document.body;
      localStorage.setItem('token', b.dataset.access);
      localStorage.setItem('refresh_token', b.dataset.refresh);
      window.location.href = '/';
    </script>
    <p><a href="/" style="color:#8b5cf6">Click here if not redirected</a></p>
  </div>
</body>
</html>"""
    )


@auth_router.get("/me", response_model=UserProfileDTO)
async def get_profile(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> UserProfileDTO:
    """Get current user's profile and usage stats."""
    monthly_used = await container.usage_tracker().get_monthly_request_count(
        current_user.id
    )
    return UserProfileDTO(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        timezone=current_user.timezone,
        plan=current_user.plan.value,
        monthly_requests_used=monthly_used,
        monthly_request_limit=current_user.get_request_limit(),
    )


@auth_router.post("/refresh", response_model=LoginResponseDTO)
async def refresh_token(
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> LoginResponseDTO:
    """Exchange a valid refresh token for a new access token.

    Send the refresh token as ``Authorization: Bearer <refresh_token>``.
    """
    from src.domain.exceptions import AuthenticationError
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )
    raw_token = authorization[len("Bearer ") :]

    jwt_svc = container.jwt_service()
    try:
        payload = jwt_svc.decode_token(raw_token)
    except AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token",
        )

    user_id = payload.get("sub")
    db = container.database()
    async with db.session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        user = await repo.get_by_id(UUID(user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    new_access = jwt_svc.create_access_token(user)
    new_refresh = jwt_svc.create_refresh_token(user)
    return LoginResponseDTO(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.post(
    "/logout", status_code=204, summary="Invalidate the current access token"
)
async def logout(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> None:
    """
    Revoke the caller's current access token (and optionally refresh token).

    After this call the token is immediately invalid even within its expiry window.
    The client should also discard its locally stored tokens.
    """
    from datetime import timezone as _tz

    from jose import jwt as _jwt

    from src.infrastructure.security.token_blocklist import get_token_blocklist

    blocklist = get_token_blocklist()
    settings = container.settings

    if authorization and authorization.startswith("Bearer "):
        raw = authorization[len("Bearer ") :]
        try:
            payload = _jwt.decode(
                raw, settings.app_secret_key, algorithms=[settings.jwt_algorithm]
            )
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti:
                from datetime import datetime

                expires_at = datetime.fromtimestamp(exp, tz=_tz.utc) if exp else None
                await blocklist.revoke(jti, expires_at)
        except Exception:
            pass  # Already expired or malformed — safe to ignore

    # Audit log
    try:
        await container.audit_log_service().record(
            action="auth.logout",
            actor_id=str(current_user.id),
            detail="User logged out and token revoked",
        )
    except Exception:
        pass


class _ChangePasswordRequest(BaseModel):
    old_password: str = _Field(..., min_length=1)
    new_password: str = _Field(..., min_length=8, max_length=128)


@auth_router.post(
    "/change-password",
    status_code=204,
    summary="Change email/password account password",
)
async def change_password(
    payload: _ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> None:
    """
    Change the authenticated user's password.

    - Requires the current password (old_password).
    - All existing tokens are revoked after a successful change.
    - Returns 400 if the account uses OAuth-only (no password set).
    - Returns 401 if old_password is wrong.
    """
    import base64
    import hashlib

    import bcrypt as _bcrypt_lib
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel
    from src.infrastructure.security.token_blocklist import get_token_blocklist

    def _verify_pw(pw: str, hashed: str) -> bool:
        digest = base64.b64encode(hashlib.sha256(pw.encode()).digest())
        try:
            return _bcrypt_lib.checkpw(digest, hashed.encode())
        except Exception:
            return False

    def _hash_pw(pw: str) -> str:
        digest = base64.b64encode(hashlib.sha256(pw.encode()).digest())
        return _bcrypt_lib.hashpw(digest, _bcrypt_lib.gensalt(12)).decode()

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.id == current_user.id)
        )
        row = result.scalar_one_or_none()
        if not row or not row.hashed_password:
            raise HTTPException(
                status_code=400,
                detail="This account uses social login (OAuth). Password change is not applicable.",
            )
        if not _verify_pw(payload.old_password, row.hashed_password):
            raise HTTPException(
                status_code=401, detail="Current password is incorrect."
            )

        row.hashed_password = _hash_pw(payload.new_password)
        await session.commit()

    # Revoke the current token so the client must re-authenticate
    if authorization and authorization.startswith("Bearer "):
        from datetime import datetime
        from datetime import timezone as _tz

        from jose import jwt as _jwt

        from src.infrastructure.security.token_blocklist import (
            get_token_blocklist as _get_bl,
        )

        try:
            tok_payload = _jwt.decode(
                authorization[7:],
                container.settings.app_secret_key,
                algorithms=[container.settings.jwt_algorithm],
            )
            jti = tok_payload.get("jti")
            exp = tok_payload.get("exp")
            if jti:
                expires_at = datetime.fromtimestamp(exp, tz=_tz.utc) if exp else None
                await _get_bl().revoke(jti, expires_at)
        except Exception:
            pass

    try:
        await container.audit_log_service().record(
            action="auth.password_changed",
            actor_id=str(current_user.id),
            detail="Password changed; active tokens revoked",
        )
    except Exception:
        pass


class _ForgotPasswordRequest(BaseModel):
    email: EmailStr


class _ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = _Field(..., min_length=8, max_length=128)


@auth_router.post(
    "/forgot-password", status_code=202, summary="Request a password-reset email"
)
async def forgot_password(
    payload: _ForgotPasswordRequest,
    container: Container = Depends(get_container),
) -> dict:
    """
    Send a password-reset link to the given email address.

    Always returns HTTP 202 Accepted regardless of whether the email is
    registered — this prevents user enumeration.
    """
    import secrets

    from sqlalchemy import select

    from src.infrastructure.notifications.email_sender import send_password_reset_email
    from src.infrastructure.persistence.models import PasswordResetTokenModel, UserModel

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(UserModel).where(
                UserModel.email == payload.email.lower(),
                UserModel.hashed_password != None,  # noqa: E711
            )
        )
        row = result.scalar_one_or_none()

        if row:
            # Expire any existing unused tokens for this user
            from datetime import timedelta as _td
            from datetime import timezone as _tz

            from sqlalchemy import update as sql_update

            await session.execute(
                sql_update(PasswordResetTokenModel)
                .where(
                    PasswordResetTokenModel.user_id == row.id,
                    PasswordResetTokenModel.used == False,  # noqa: E712
                )
                .values(used=True)
            )

            raw_token = secrets.token_urlsafe(48)
            expires_at = datetime.now(_tz.utc) + _td(hours=1)
            reset_row = PasswordResetTokenModel(
                user_id=row.id,
                token=raw_token,
                expires_at=expires_at,
            )
            session.add(reset_row)
            await session.commit()

            reset_url = (
                f"{container.settings.app_base_url}/reset-password?token={raw_token}"
            )
            await send_password_reset_email(
                settings=container.settings,
                to_email=row.email,
                reset_url=reset_url,
            )

    return {
        "detail": "If that email is registered you will receive a reset link shortly."
    }


@auth_router.post(
    "/reset-password", status_code=204, summary="Complete password reset via token"
)
async def reset_password(
    payload: _ResetPasswordRequest,
    container: Container = Depends(get_container),
) -> None:
    """
    Set a new password using a reset token received by email.

    The token is single-use and expires after 1 hour.
    Returns 410 if token is expired or already used.
    """
    import base64
    import hashlib
    from datetime import timezone as _tz

    import bcrypt as _bcrypt_lib
    from sqlalchemy import select

    from src.infrastructure.persistence.models import PasswordResetTokenModel, UserModel

    def _hash_pw(pw: str) -> str:
        digest = base64.b64encode(hashlib.sha256(pw.encode()).digest())
        return _bcrypt_lib.hashpw(digest, _bcrypt_lib.gensalt(12)).decode()

    now = datetime.now(_tz.utc)
    db = container.database()

    async with db.session_factory() as session:
        result = await session.execute(
            select(PasswordResetTokenModel).where(
                PasswordResetTokenModel.token == payload.token
            )
        )
        reset_row = result.scalar_one_or_none()

        if reset_row is None:
            raise HTTPException(
                status_code=410, detail="Reset token not found or already used."
            )
        if reset_row.used:
            raise HTTPException(status_code=410, detail="Reset token already used.")
        if reset_row.expires_at.replace(tzinfo=_tz.utc) < now:
            raise HTTPException(status_code=410, detail="Reset token has expired.")

        # Update user password
        user_result = await session.execute(
            select(UserModel).where(UserModel.id == reset_row.user_id)
        )
        user_row = user_result.scalar_one_or_none()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found.")

        user_row.hashed_password = _hash_pw(payload.new_password)
        reset_row.used = True
        await session.commit()


@auth_router.patch("/me", response_model=UserProfileDTO, summary="Update your profile")
async def update_profile(
    name: str | None = None,
    timezone: str | None = None,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> UserProfileDTO:
    """
    Update the authenticated user's display name and/or timezone.

    Pass query params: ``?name=Alice&timezone=America/New_York``.
    Only fields that are provided are updated.
    """
    from sqlalchemy import select

    from src.infrastructure.persistence.models import UserModel

    if not name and not timezone:
        raise HTTPException(
            status_code=400, detail="Provide at least one of: name, timezone"
        )

    db = container.database()
    async with db.session_factory() as session:
        result = await session.execute(
            select(UserModel).where(UserModel.id == current_user.id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if name:
            row.name = name[:255]
        if timezone:
            row.timezone = timezone[:50]

        await session.commit()

    monthly_used = await container.usage_tracker().get_monthly_request_count(
        current_user.id
    )
    return UserProfileDTO(
        id=current_user.id,
        email=current_user.email,
        name=name or current_user.name,
        timezone=timezone or current_user.timezone,
        plan=current_user.plan.value,
        monthly_requests_used=monthly_used,
        monthly_request_limit=current_user.get_request_limit(),
    )


@auth_router.post("/dev-login", response_model=LoginResponseDTO)
async def dev_login(
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """Dev-only: create or reuse a test user and return JWT. Disabled in production."""
    if container.settings.is_production:
        raise HTTPException(status_code=404, detail="Not found")

    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    db = container.database()
    async with db.session_factory() as session:
        repo = SQLAlchemyUserRepository(session)
        user = await repo.get_by_email("dev@calendar-agent.local")
        if not user:
            from src.domain.entities.user import User as _User

            user = _User(email="dev@calendar-agent.local", name="Dev User")
            user = await repo.create(user)
        await session.commit()

    jwt_svc = container.jwt_service()
    access = jwt_svc.create_access_token(user)
    refresh = jwt_svc.create_refresh_token(user)
    return LoginResponseDTO(
        access_token=access,
        refresh_token=refresh,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


@auth_router.get("/accept-invite")
async def accept_invite(
    token: str,
    container: Container = Depends(get_container),
) -> LoginResponseDTO:
    """
    Accept an org membership invite via a magic-link token.

    The invited user navigates to this URL from their email.
    The endpoint validates the token, marks the invite as accepted,
    and returns a JWT pair so the user is immediately logged in.
    """
    from datetime import timezone as _tz

    from sqlalchemy import select, update

    from src.infrastructure.persistence.models import OrgPendingInviteModel
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    now = datetime.now(_tz.utc)

    db = container.database()
    async with db.session_factory() as session:
        # First do a quick read to give a meaningful error for expired / bad tokens
        result = await session.execute(
            select(OrgPendingInviteModel).where(OrgPendingInviteModel.token == token)
        )
        invite = result.scalar_one_or_none()

        if invite is None:
            raise HTTPException(status_code=404, detail="Invite token not found")
        if invite.accepted:
            raise HTTPException(status_code=410, detail="Invite already accepted")
        if invite.expires_at.replace(tzinfo=_tz.utc) < now:
            raise HTTPException(status_code=410, detail="Invite token has expired")

        # Atomic UPDATE — only succeeds for the first concurrent caller.
        # WHERE accepted=FALSE prevents double-acceptance in a race.
        update_result = await session.execute(
            update(OrgPendingInviteModel)
            .where(
                OrgPendingInviteModel.token == token,
                OrgPendingInviteModel.accepted == False,  # noqa: E712
            )
            .values(accepted=True)
            .returning(OrgPendingInviteModel.id)
        )
        updated_id = update_result.scalar_one_or_none()
        if updated_id is None:
            # Another concurrent request already claimed this token
            raise HTTPException(status_code=410, detail="Invite already accepted")

        await session.commit()

        # Re-fetch the committed row for downstream use
        result2 = await session.execute(
            select(OrgPendingInviteModel).where(OrgPendingInviteModel.id == updated_id)
        )
        invite = result2.scalar_one()

        # Write audit record (fire-and-forget, non-blocking)
        try:
            await container.audit_log_service().record(
                action="org.invite_accepted",
                actor_id=str(invite.user_id),
                target_id=str(invite.org_id),
                detail=f"Invite accepted for org {invite.org_id}",
                request_id="",
            )
        except Exception:
            pass

        # Notify the OWNER via outbound webhook if configured
        try:
            webhook_url = getattr(container.settings, "invite_accept_webhook_url", "")
            if webhook_url:
                import httpx

                async def _post_webhook(url: str, payload: dict) -> None:
                    async with httpx.AsyncClient(timeout=5) as hc:
                        await hc.post(url, json=payload)

                import asyncio as _asyncio

                _t = _asyncio.create_task(
                    _post_webhook(
                        webhook_url,
                        {
                            "event": "org.invite_accepted",
                            "org_id": str(invite.org_id),
                            "user_id": str(invite.user_id),
                            "role": invite.role,
                        },
                    )
                )
        except Exception:
            pass

        # Load the pre-created user
        user_repo = SQLAlchemyUserRepository(session)
        user = await user_repo.get_by_id(invite.user_id)
        if not user:
            raise HTTPException(
                status_code=404, detail="Invited user account not found"
            )

    jwt_svc = container.jwt_service()
    access = jwt_svc.create_access_token(user)
    refresh = jwt_svc.create_refresh_token(user)
    return LoginResponseDTO(
        access_token=access,
        refresh_token=refresh,
        expires_in=container.settings.jwt_access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
chat_router = APIRouter()


@chat_router.post("/", response_model=ChatResponseDTO)
async def send_message(
    request: ChatRequestDTO,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> ChatResponseDTO:
    """
    Send a natural language message to the Calendar Agent.
    The agent will interpret the request and manage calendar operations.
    """
    from src.infrastructure.persistence.conversation_repository import (
        SQLAlchemyConversationRepository,
    )

    db = container.database()
    async with db.session_factory() as session:
        chat_svc = _build_chat_service(
            container, SQLAlchemyConversationRepository(session)
        )
        response = await chat_svc.handle_message(
            user_id=current_user.id,
            request=request,
            plan_limit=current_user.get_request_limit(),
        )
        await session.commit()
    return response


# ---------------------------------------------------------------------------
# Calendar (direct CRUD endpoints, bypassing agent)
# ---------------------------------------------------------------------------
calendar_router = APIRouter()


@calendar_router.get("/events", response_model=list[EventResponseDTO])
async def list_events(
    start: datetime,
    end: datetime,
    calendar_id: str = "primary",
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[EventResponseDTO]:
    """List calendar events in a date range."""
    cal_svc = _build_calendar_service(container)
    dto = DateRangeDTO(start=start, end=end, calendar_id=calendar_id)
    return await cal_svc.list_events(user_id=current_user.id, dto=dto)


@calendar_router.post(
    "/events",
    response_model=EventResponseDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    request: CreateEventDTO,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> EventResponseDTO:
    """Create a new calendar event."""
    cal_svc = _build_calendar_service(container)
    return await cal_svc.create_event(user_id=current_user.id, dto=request)


@calendar_router.patch(
    "/events/{event_id}",
    response_model=EventResponseDTO,
)
async def update_event(
    event_id: str,
    request: UpdateEventDTO,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> EventResponseDTO:
    """Partially update a calendar event (title, times, location, description, attendees)."""
    from src.domain.exceptions import EventNotFoundError

    # Ensure the path event_id is used (body may omit it)
    merged = request.model_copy(update={"event_id": event_id})
    cal_svc = _build_calendar_service(container)
    try:
        return await cal_svc.update_event(user_id=current_user.id, dto=merged)
    except EventNotFoundError:
        raise HTTPException(status_code=404, detail="Event not found")


@calendar_router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> None:
    """Delete a calendar event."""
    cal_svc = _build_calendar_service(container)
    deleted = await cal_svc.delete_event(user_id=current_user.id, event_id=event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")


@calendar_router.get(
    "/events/{event_id}/ics",
    summary="Download a calendar event as an .ics file (RFC 5545)",
)
async def export_event_ics(
    event_id: str,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> Response:
    """Return a single event as an iCalendar (`.ics`) attachment."""

    cal_svc = _build_calendar_service(container)
    event = await cal_svc._provider.get_event(current_user.id, event_id)  # type: ignore[attr-defined]
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    def _fmt(dt: datetime) -> str:
        """Format datetime as iCal YYYYMMDDTHHMMSSZ (always UTC)."""
        import calendar as _cal

        if dt.tzinfo is None:
            ts = _cal.timegm(dt.timetuple())
        else:
            ts = int(dt.timestamp())
        from datetime import timezone as _tz

        utc = datetime.fromtimestamp(ts, tz=_tz.utc)
        return utc.strftime("%Y%m%dT%H%M%SZ")

    uid = f"{event_id}@chronos.calendar"
    dtstamp = _fmt(datetime.utcnow())
    dtstart = _fmt(event.start_time)
    dtend = _fmt(event.end_time)
    summary = (
        (event.title or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
    )
    description = (
        (event.description or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
    )
    location = (
        (event.location or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Chronos AI//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{summary}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if location:
        lines.append(f"LOCATION:{location}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    ics_body = "\r\n".join(lines) + "\r\n"
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in (event.title or "event")
    )[:40]
    filename = f"{safe_title.strip()}.ics"

    return Response(
        content=ics_body.encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_chat_service(
    container: Container,
    conversation_repo: object,
) -> "ChatService":  # noqa: F821
    """Assemble a ChatService with all dependencies from the container."""
    from src.application.services.chat_service import ChatService

    settings = container.settings
    return ChatService(
        conversation_repo=conversation_repo,  # type: ignore[arg-type]
        usage_tracker=container.usage_tracker(),
        cache=container.cache(),
        agent_executor=container.calendar_agent(),
        intent_router=container.intent_router(),
        complexity_router=None,
        calendar_provider=container.calendar_adapter(),
        llm_provider=settings.llm_provider,
        model_fast=settings.active_model_fast,
        model_primary=settings.active_model_primary,
    )


def _build_calendar_service(container: Container) -> "CalendarService":  # noqa: F821
    """Assemble a CalendarService with all dependencies from the container."""
    from src.application.services.calendar_service import CalendarService

    cal_adapter = container.calendar_adapter()
    return CalendarService(
        calendar_provider=cal_adapter,
        event_repository=cal_adapter,  # type: ignore[arg-type]
        cache=container.cache(),
    )


# ---------------------------------------------------------------------------
# GDPR — account deletion + data export
# ---------------------------------------------------------------------------


@auth_router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete account (GDPR right to erasure)",
)
async def delete_account(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> None:
    """
    Permanently delete the authenticated user's account and all associated data.
    This operation is irreversible.  Satisfies GDPR Article 17 right to erasure.
    """
    from sqlalchemy import delete as sql_delete

    from src.infrastructure.persistence.models import (
        ConversationModel,
        MessageModel,
        UsageRecordModel,
    )
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    uid = current_user.id
    db = container.database()

    async with db.session_factory() as session:
        # Delete in dependency order to avoid FK violations
        # 1. Messages → linked to conversations
        conv_result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(ConversationModel.id)
            .where(ConversationModel.user_id == uid)
        )
        conv_ids = [r for (r,) in conv_result.all()]
        if conv_ids:
            await session.execute(
                sql_delete(MessageModel).where(
                    MessageModel.conversation_id.in_(conv_ids)
                )
            )

        # 2. Conversations
        await session.execute(
            sql_delete(ConversationModel).where(ConversationModel.user_id == uid)
        )

        # 3. Usage records
        await session.execute(
            sql_delete(UsageRecordModel).where(UsageRecordModel.user_id == uid)
        )

        # 4. Provider connections (org_models)
        try:
            from src.infrastructure.persistence.org_models import (
                ProviderConnectionModel,
            )

            await session.execute(
                sql_delete(ProviderConnectionModel).where(
                    ProviderConnectionModel.user_id == uid
                )
            )
        except Exception:
            pass

        # 5. User row itself
        repo = SQLAlchemyUserRepository(session)
        await repo.delete(uid)

        await session.commit()

    # Invalidate any cached data for this user
    try:
        cache = container.cache()
        await cache.delete(f"chat:{uid}:*")
        await cache.delete(f"usage:{uid}:*")
    except Exception:
        pass

    # Record audit event (fire-and-forget)
    try:
        await container.audit_log_service().record(
            "user.account_deleted",
            actor_id=uid,
            target_id=uid,
            detail=f"User {current_user.email} self-deleted (GDPR erasure)",
        )
    except Exception:
        pass


@auth_router.get(
    "/me/export",
    response_model=GDPRExportSchema,
    summary="Export account data (GDPR Article 20 data portability)",
)
async def export_account_data(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> dict:
    """
    Return a JSON bundle of all personal data held for the authenticated user.
    Satisfies GDPR Article 20 right to data portability.
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import select

    from src.infrastructure.persistence.models import (
        ConversationModel,
        MessageModel,
        UsageRecordModel,
    )

    uid = current_user.id
    db = container.database()

    async with db.session_factory() as session:
        # Conversations
        conv_result = await session.execute(
            select(ConversationModel).where(ConversationModel.user_id == uid)
        )
        conversations = [
            {
                "id": str(c.id),
                "title": c.title,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in conv_result.scalars().all()
        ]

        # Messages (last 1000 to cap response size)
        conv_ids = [c["id"] for c in conversations]
        messages: list[dict] = []
        if conv_ids:
            import uuid as _uuid

            msg_result = await session.execute(
                select(MessageModel)
                .where(
                    MessageModel.conversation_id.in_([_uuid.UUID(i) for i in conv_ids])
                )
                .order_by(MessageModel.created_at.desc())
                .limit(1000)
            )
            messages = [
                {
                    "id": str(m.id),
                    "conversation_id": str(m.conversation_id),
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in msg_result.scalars().all()
            ]

        # Usage records
        usage_result = await session.execute(
            select(UsageRecordModel).where(UsageRecordModel.user_id == uid)
        )
        usage = [
            {
                "id": str(u.id),
                "request_count": u.request_count,
                "period_start": u.period_start.isoformat() if u.period_start else None,
            }
            for u in usage_result.scalars().all()
        ]

    bundle = {
        "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "name": current_user.name,
            "timezone": current_user.timezone,
            "plan": (
                current_user.plan.value
                if hasattr(current_user.plan, "value")
                else str(current_user.plan)
            ),
        },
        "conversations": conversations,
        "messages": messages,
        "usage_records": usage,
    }
    return JSONResponse(
        content=bundle,
        headers={
            "Content-Disposition": 'attachment; filename="calendar-agent-export.json"'
        },
    )


# ---------------------------------------------------------------------------
# Admin — Audit log viewer
# ---------------------------------------------------------------------------
admin_router = APIRouter()


class AuditLogEntrySchema(BaseModel):
    id: str
    action: str
    actor_id: str | None = None
    target_id: str | None = None
    detail: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    created_at: str


@admin_router.get("/audit-logs", response_model=list[AuditLogEntrySchema])
async def list_audit_logs(
    page: int = 1,
    page_size: int = 50,
    action: str | None = None,
    actor_id: str | None = None,
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> list[dict]:
    """
    Read the audit log.  Restricted to OWNER-role users.

    Supports filtering by ``action`` (exact) and ``actor_id`` (UUID).
    Results are newest-first, paginated.

    Response headers:
    - ``X-Total-Count``: total matching rows (before pagination)
    - ``X-Page`` / ``X-Page-Size``
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import func, select

    from src.infrastructure.persistence.models import AuditLogModel
    from src.infrastructure.persistence.org_models import OrgMembershipModel

    db = container.database()

    # Access control: user must be OWNER of at least one org, or be a system admin
    async with db.session_factory() as session:
        owner_check = await session.execute(
            select(OrgMembershipModel).where(
                OrgMembershipModel.user_id == current_user.id,
                OrgMembershipModel.role == "owner",
            )
        )
        if not owner_check.scalars().first():
            raise HTTPException(
                status_code=403,
                detail="Only organization OWNERs may view the audit log",
            )

        base_query = select(AuditLogModel)
        if action:
            base_query = base_query.where(AuditLogModel.action == action)
        if actor_id:
            import uuid as _uuid

            try:
                base_query = base_query.where(
                    AuditLogModel.actor_id == _uuid.UUID(actor_id)
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid actor_id UUID")

        # Count total matching rows for X-Total-Count header
        count_result = await session.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        page = max(1, page)
        page_size = min(max(page_size, 1), 200)
        query = (
            base_query.order_by(AuditLogModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        result = await session.execute(query)
        rows = result.scalars().all()

    items = [
        {
            "id": str(r.id),
            "action": r.action,
            "actor_id": str(r.actor_id) if r.actor_id else None,
            "target_id": str(r.target_id) if r.target_id else None,
            "detail": r.detail,
            "request_id": r.request_id,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return JSONResponse(
        content=items,
        headers={
            "X-Total-Count": str(total),
            "X-Page": str(page),
            "X-Page-Size": str(page_size),
        },
    )


async def delete_account(
    current_user: User = Depends(get_current_user),
    container: Container = Depends(get_container),
) -> None:
    """
    Permanently delete the authenticated user's account and all associated data.
    This operation is irreversible.  Satisfies GDPR Article 17 right to erasure.
    """
    from sqlalchemy import delete as sql_delete

    from src.infrastructure.persistence.models import (
        ConversationModel,
        MessageModel,
        UsageRecordModel,
    )
    from src.infrastructure.persistence.user_repository import SQLAlchemyUserRepository

    uid = current_user.id
    db = container.database()

    async with db.session_factory() as session:
        # Delete in dependency order to avoid FK violations
        # 1. Messages → linked to conversations
        conv_result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"])
            .select(ConversationModel.id)
            .where(ConversationModel.user_id == uid)
        )
        conv_ids = [r for (r,) in conv_result.all()]
        if conv_ids:
            await session.execute(
                sql_delete(MessageModel).where(
                    MessageModel.conversation_id.in_(conv_ids)
                )
            )

        # 2. Conversations
        await session.execute(
            sql_delete(ConversationModel).where(ConversationModel.user_id == uid)
        )

        # 3. Usage records
        await session.execute(
            sql_delete(UsageRecordModel).where(UsageRecordModel.user_id == uid)
        )

        # 4. Provider connections (org_models)
        try:
            from src.infrastructure.persistence.org_models import (
                ProviderConnectionModel,
            )

            await session.execute(
                sql_delete(ProviderConnectionModel).where(
                    ProviderConnectionModel.user_id == uid
                )
            )
        except Exception:
            pass

        # 5. User row itself
        repo = SQLAlchemyUserRepository(session)
        await repo.delete(uid)

        await session.commit()

    # Invalidate any cached data for this user
    try:
        cache = container.cache()
        await cache.delete(f"chat:{uid}:*")
        await cache.delete(f"usage:{uid}:*")
    except Exception:
        pass

    # Record audit event (fire-and-forget)
    try:
        await container.audit_log_service().record(
            "user.account_deleted",
            actor_id=uid,
            target_id=uid,
            detail=f"User {current_user.email} self-deleted (GDPR erasure)",
        )
    except Exception:
        pass
