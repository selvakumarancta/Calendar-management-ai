"""
Auth Service — handles user authentication, OAuth, and JWT token management.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.domain.entities.user import User
from src.domain.exceptions import AuthenticationError, TokenExpiredError
from src.domain.interfaces.user_repository import UserRepositoryPort


class AuthService:
    """Application-level authentication orchestration."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        jwt_secret: str,
        jwt_algorithm: str = "HS256",
        access_token_expire_minutes: int = 30,
        refresh_token_expire_days: int = 7,
    ) -> None:
        self._user_repo = user_repository
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm
        self._access_expire_minutes = access_token_expire_minutes
        self._refresh_expire_days = refresh_token_expire_days

    async def authenticate_google_oauth(
        self,
        email: str,
        name: str,
        access_token: str,
        refresh_token: str | None,
        token_expiry: datetime,
    ) -> tuple[User, str, str]:
        """
        Handle Google OAuth callback.
        Returns (user, jwt_access_token, jwt_refresh_token).
        """
        # Find or create user
        user = await self._user_repo.get_by_email(email)
        if user is None:
            user = User(email=email, name=name)

        # Update Google tokens
        user.update_google_tokens(access_token, refresh_token, token_expiry)
        if user.id:
            user = await self._user_repo.update(user)
        else:
            user = await self._user_repo.create(user)

        # Issue JWT
        jwt_access = self._create_access_token(user)
        jwt_refresh = self._create_refresh_token(user)

        return user, jwt_access, jwt_refresh

    async def get_user_from_token(self, token: str) -> User:
        """Validate JWT and return the associated user."""
        payload = self._decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise AuthenticationError("Invalid token payload")

        user = await self._user_repo.get_by_id(UUID(user_id))
        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive")

        return user

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """Issue new access + refresh tokens from a valid refresh token."""
        user = await self.get_user_from_token(refresh_token)
        return self._create_access_token(user), self._create_refresh_token(user)

    def _create_access_token(self, user: User) -> str:
        """Create a JWT access token. Implemented by infra JWT adapter."""
        # This is a placeholder — actual JWT encoding is in the infrastructure layer
        # to keep the application layer free of jose/jwt library dependency.
        raise NotImplementedError("JWT encoding delegated to infrastructure")

    def _create_refresh_token(self, user: User) -> str:
        """Create a JWT refresh token."""
        raise NotImplementedError("JWT encoding delegated to infrastructure")

    def _decode_token(self, token: str) -> dict:
        """Decode and validate a JWT token."""
        raise NotImplementedError("JWT decoding delegated to infrastructure")
