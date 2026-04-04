"""
JWT Service — infrastructure adapter for JSON Web Token operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from src.domain.entities.user import User
from src.domain.exceptions import AuthenticationError


class JWTService:
    """Handles JWT creation and validation."""

    def __init__(
        self,
        secret_key: str,
        algorithm: str = "HS256",
        access_token_expire_minutes: int = 30,
        refresh_token_expire_days: int = 7,
    ) -> None:
        self._secret = secret_key
        self._algorithm = algorithm
        self._access_expire = timedelta(minutes=access_token_expire_minutes)
        self._refresh_expire = timedelta(days=refresh_token_expire_days)

    def create_access_token(self, user: User) -> str:
        """Create a short-lived access token."""
        expire = datetime.now(timezone.utc) + self._access_expire
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "plan": user.plan.value,
            "type": "access",
            "exp": expire,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def create_refresh_token(self, user: User) -> str:
        """Create a long-lived refresh token."""
        expire = datetime.now(timezone.utc) + self._refresh_expire
        payload = {
            "sub": str(user.id),
            "type": "refresh",
            "exp": expire,
        }
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode_token(self, token: str) -> dict:
        """Decode and validate a JWT token."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            return payload
        except JWTError as e:
            raise AuthenticationError(f"Invalid token: {e}") from e
