"""
Audit Log Service — records privileged mutations for compliance and forensics.
All writes are append-only (no update/delete on audit_logs table).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("calendar_agent.audit")


class AuditLogService:
    """
    Fire-and-forget audit event recorder.
    Call ``record()`` from route handlers or application services whenever
    a privileged mutation occurs.
    """

    def __init__(self, db_session_factory: Any) -> None:
        self._session_factory = db_session_factory

    async def record(
        self,
        action: str,
        *,
        actor_id: uuid.UUID | None = None,
        target_id: uuid.UUID | None = None,
        detail: str | None = None,
        metadata: dict | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """
        Append an audit event.  Failures are logged but never raised —
        a failed audit write must not abort the main transaction.
        """
        from src.infrastructure.persistence.models import AuditLogModel

        # Capture request_id from context var if not explicitly provided
        if request_id is None:
            try:
                from src.api.middleware.correlation_id import request_id_var

                request_id = request_id_var.get("") or None
            except Exception:
                pass

        # Capture client IP from context var if not explicitly provided
        if ip_address is None:
            try:
                from src.api.middleware.correlation_id import client_ip_var

                ip_address = client_ip_var.get("") or None
            except Exception:
                pass

        try:
            async with self._session_factory() as session:
                # Coerce str to UUID so SQLAlchemy's Uuid column type is satisfied
                def _to_uuid(v: Any) -> uuid.UUID | None:
                    if v is None:
                        return None
                    if isinstance(v, uuid.UUID):
                        return v
                    try:
                        return uuid.UUID(str(v))
                    except (ValueError, AttributeError):
                        return None

                log_entry = AuditLogModel(
                    id=uuid.uuid4(),
                    actor_id=_to_uuid(actor_id),
                    target_id=_to_uuid(target_id),
                    action=action,
                    detail=detail,
                    metadata_json=(
                        json.dumps(metadata, default=str) if metadata else None
                    ),
                    request_id=request_id,
                    ip_address=ip_address,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(log_entry)
                await session.commit()
                logger.info(
                    "audit",
                    extra={
                        "audit_action": action,
                        "actor": str(actor_id),
                        "target": str(target_id),
                        "request_id": request_id,
                    },
                )
        except Exception as exc:
            # Never let an audit failure propagate — log and continue
            logger.error("Failed to write audit log: %s", exc)
