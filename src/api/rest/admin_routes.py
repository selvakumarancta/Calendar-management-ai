"""
Admin API Routes — read-only oversight of all users, events and suggestions.

Admins can VIEW everything but CANNOT approve/reject suggestions or create
events on behalf of users. That must be done by the users themselves.

RBAC enforcement: all routes require system_role = admin | superadmin.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db_session, require_admin
from src.domain.entities.user import SystemRole, User

admin_rbac_router = APIRouter()


# ---------------------------------------------------------------------------
# Users overview
# ---------------------------------------------------------------------------


@admin_rbac_router.get("/users", summary="List all users (admin)")
async def admin_list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    role: str | None = Query(None, description="Filter by system_role"),
    session: AsyncSession = Depends(get_db_session),
    _admin: User = Depends(require_admin),
) -> list[dict]:
    """Return all registered users with their roles, plans and timezones."""
    from src.infrastructure.persistence.models import UserModel

    query = select(UserModel).order_by(UserModel.created_at.desc()).limit(limit).offset(offset)
    if role:
        query = query.where(UserModel.system_role == role)

    result = await session.execute(query)
    users = result.scalars().all()

    return [
        {
            "id": str(u.id),
            "email": u.email,
            "name": u.name,
            "timezone": u.timezone,
            "plan": u.plan,
            "system_role": getattr(u, "system_role", "user"),
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@admin_rbac_router.patch("/users/{user_id}/role", summary="Change a user's system role (superadmin)")
async def admin_set_user_role(
    user_id: UUID,
    role: str,
    session: AsyncSession = Depends(get_db_session),
    admin: User = Depends(require_admin),
) -> dict:
    """
    Promote or demote a user's system role.

    Only superadmins can assign the admin/superadmin role.
    Admins can only set role=user (demote to regular user).
    """
    from src.infrastructure.persistence.models import UserModel

    valid_roles = [r.value for r in SystemRole]
    if role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"role must be one of: {valid_roles}")

    # Only superadmins can grant elevated roles
    if role in ("admin", "superadmin") and admin.system_role != SystemRole.SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only superadmins can grant admin or superadmin roles.",
        )

    result = await session.execute(select(UserModel).where(UserModel.id == user_id))
    user_model = result.scalar_one_or_none()
    if not user_model:
        raise HTTPException(status_code=404, detail="User not found")

    user_model.system_role = role
    await session.commit()

    return {"user_id": str(user_id), "system_role": role, "updated": True}


# ---------------------------------------------------------------------------
# All suggestions overview (read-only)
# ---------------------------------------------------------------------------


@admin_rbac_router.get("/suggestions", summary="All suggestions across all users (admin)")
async def admin_list_suggestions(
    status: str | None = Query(None),
    user_id: UUID | None = Query(None, description="Filter by specific user"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    _admin: User = Depends(require_admin),
) -> list[dict]:
    """Read-only view of ALL schedule suggestions across ALL users."""
    from src.infrastructure.persistence.email_models import ScheduleSuggestionModel

    query = (
        select(ScheduleSuggestionModel)
        .order_by(ScheduleSuggestionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(ScheduleSuggestionModel.status == status)
    if user_id:
        query = query.where(ScheduleSuggestionModel.user_id == user_id)

    result = await session.execute(query)
    rows = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "email_subject": r.email_subject,
            "email_sender": r.email_sender,
            "title": r.title,
            "status": r.status,
            "proposed_start": r.proposed_start.isoformat() if r.proposed_start else None,
            "proposed_end": r.proposed_end.isoformat() if r.proposed_end else None,
            "calendar_event_id": r.calendar_event_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# All calendar events overview (read-only)
# ---------------------------------------------------------------------------


@admin_rbac_router.get("/events", summary="All calendar events across all users (admin)")
async def admin_list_events(
    user_id: UUID | None = Query(None, description="Filter by specific user"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    _admin: User = Depends(require_admin),
) -> list[dict]:
    """Read-only view of ALL calendar events across ALL users."""
    from src.infrastructure.persistence.calendar_models import CalendarEventModel

    query = (
        select(CalendarEventModel)
        .order_by(CalendarEventModel.start_time.desc())
        .limit(limit)
        .offset(offset)
    )
    if user_id:
        query = query.where(CalendarEventModel.user_id == user_id)

    result = await session.execute(query)
    rows = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "title": r.title,
            "provider_event_id": r.provider_event_id,
            "start_time": r.start_time.isoformat() if r.start_time else None,
            "end_time": r.end_time.isoformat() if r.end_time else None,
            "status": r.status,
            "source": r.source,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Stats dashboard
# ---------------------------------------------------------------------------


@admin_rbac_router.get("/stats", summary="Platform-wide statistics (admin)")
async def admin_stats(
    session: AsyncSession = Depends(get_db_session),
    _admin: User = Depends(require_admin),
) -> dict:
    """High-level platform stats: user counts, suggestion counts, event counts."""
    from src.infrastructure.persistence.email_models import ScheduleSuggestionModel
    from src.infrastructure.persistence.models import UserModel

    total_users = (await session.execute(select(func.count()).select_from(UserModel))).scalar_one()
    active_users = (await session.execute(
        select(func.count()).select_from(UserModel).where(UserModel.is_active == True)  # noqa: E712
    )).scalar_one()

    total_suggestions = (await session.execute(
        select(func.count()).select_from(ScheduleSuggestionModel)
    )).scalar_one()

    pending_suggestions = (await session.execute(
        select(func.count()).select_from(ScheduleSuggestionModel).where(
            ScheduleSuggestionModel.status == "pending"
        )
    )).scalar_one()

    approved_suggestions = (await session.execute(
        select(func.count()).select_from(ScheduleSuggestionModel).where(
            ScheduleSuggestionModel.status == "approved"
        )
    )).scalar_one()

    # Role breakdown
    role_rows = (await session.execute(
        select(UserModel.system_role, func.count()).group_by(UserModel.system_role)
    )).all()
    role_counts = {row[0]: row[1] for row in role_rows}

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "by_role": role_counts,
        },
        "suggestions": {
            "total": total_suggestions,
            "pending": pending_suggestions,
            "approved": approved_suggestions,
        },
    }
