"""
SQLAlchemy ORM model for the runtime configuration store.
Persists key/value settings that override .env defaults via the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.persistence.database import Base


class ConfigSettingModel(Base):
    """Key-value configuration store — UI-editable settings."""

    __tablename__ = "config_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
