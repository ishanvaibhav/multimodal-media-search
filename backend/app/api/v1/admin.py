"""Admin endpoints — user management, stats, audit (plan §40, §42, §44).

Every handler is protected by an explicit permission; destructive mutations
write audit rows. This is the backend enforcement point — the admin UI merely
mirrors these capabilities (plan §70 — rule 1).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...api.deps import require_permission
from ...api.errors import AppError, Ok
from ...auth.permissions import Permission
from ...core.audit import record_audit
from ...db.models import (
    AuditLog,
    Job,
    JobStatus,
    Media,
    MediaStatus,
    Role,
    User,
    UserStatus,
)
from ...db.session import get_db
from ...schemas.common import Page
from ...schemas.users import UserCreateIn, UserOut, UserRoleUpdateIn, UserStatusUpdateIn

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dashboard stats (plan §40)
# ---------------------------------------------------------------------------


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    total_media: int
    indexed_media: int
    processing_media: int
    active_jobs: int
    failed_jobs: int


@router.get("/stats", response_model=Ok[AdminStats])
def get_stats(
    _: User = require_permission(Permission.SYSTEM_VIEW),
    db: Session = Depends(get_db),
) -> Ok[AdminStats]:
    def _count(model, *where) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    return Ok(
        data=AdminStats(
            total_users=_count(User),
            active_users=_count(User, User.status == UserStatus.ACTIVE.value),
            total_media=_count(Media, Media.status != MediaStatus.DELETED.value),
            indexed_media=_count(Media, Media.status == MediaStatus.INDEXED.value),
            processing_media=_count(Media, Media.status == MediaStatus.PROCESSING.value),
            active_jobs=_count(
                Job,
                Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.CANCELLING.value]),
            ),
            failed_jobs=_count(Job, Job.status == JobStatus.FAILED.value),
        )
    )


# ---------------------------------------------------------------------------
# User management (plan §40 — Users tab)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=Ok[Page[UserOut]])
def list_users(
    _: User = require_permission(Permission.USER_VIEW),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    role: Role | None = None,
    status: UserStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> Ok[Page[UserOut]]:
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == role.value)
    if status is not None:
        stmt = stmt.where(User.status == status.value)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.display_name.ilike(like)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Ok(
        data=Page(
            items=[UserOut.model_validate(u) for u in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/users", response_model=Ok[UserOut], status_code=201)
def create_user(
    body: UserCreateIn,
    actor: User = require_permission(Permission.USER_CREATE),
    db: Session = Depends(get_db),
) -> Ok[UserOut]:
    """Pre-provision a user by email. No password is ever stored (plan §6) —
    the account goes live when the person signs in through Firebase and the
    verified token binds to this record."""
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise AppError.conflict("USER_EXISTS", "a user with this email already exists")

    user = User(
        email=email,
        display_name=body.display_name,
        role=body.role.value,
        status=UserStatus.PENDING.value,
        created_by=actor.id,
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="USER_CREATED",
        target_type="user",
        target_id=user.id,
        details={"email": email, "role": body.role.value},
    )
    db.commit()
    db.refresh(user)
    return Ok(data=UserOut.model_validate(user))


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError.not_found("user")
    return user


@router.patch("/users/{user_id}/role", response_model=Ok[UserOut])
def change_role(
    user_id: str,
    body: UserRoleUpdateIn,
    actor: User = require_permission(Permission.USER_UPDATE),
    db: Session = Depends(get_db),
) -> Ok[UserOut]:
    target = _get_user_or_404(db, user_id)
    if target.id == actor.id:
        raise AppError.conflict("SELF_ROLE_CHANGE", "you cannot change your own role")
    old_role = target.role
    target.role = body.role.value
    record_audit(
        db,
        actor=actor,
        action="ROLE_CHANGED",
        target_type="user",
        target_id=target.id,
        details={"email": target.email, "from": old_role, "to": body.role.value},
    )
    db.commit()
    db.refresh(target)
    return Ok(data=UserOut.model_validate(target))


@router.patch("/users/{user_id}/status", response_model=Ok[UserOut])
def change_status(
    user_id: str,
    body: UserStatusUpdateIn,
    actor: User = require_permission(Permission.USER_UPDATE),
    db: Session = Depends(get_db),
) -> Ok[UserOut]:
    if body.status == UserStatus.PENDING:
        raise AppError("INVALID_STATUS", "status can only be set to ACTIVE or DEACTIVATED", 422)

    target = _get_user_or_404(db, user_id)
    if target.id == actor.id:
        raise AppError.conflict("SELF_STATUS_CHANGE", "you cannot deactivate your own account")

    if (
        body.status == UserStatus.DEACTIVATED
        and target.role_enum == Role.ADMIN
        and target.status_enum == UserStatus.ACTIVE
    ):
        other_admins = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == Role.ADMIN.value,
                    User.status == UserStatus.ACTIVE.value,
                    User.id != target.id,
                )
            )
            or 0
        )
        if other_admins == 0:
            raise AppError.conflict("LAST_ADMIN", "cannot deactivate the last active admin")

    target.status = body.status.value
    record_audit(
        db,
        actor=actor,
        action="USER_ACTIVATED" if body.status == UserStatus.ACTIVE else "USER_DISABLED",
        target_type="user",
        target_id=target.id,
        details={"email": target.email},
    )
    db.commit()
    db.refresh(target)
    return Ok(data=UserOut.model_validate(target))


# ---------------------------------------------------------------------------
# Audit log viewer (plan §42)
# ---------------------------------------------------------------------------


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict | None
    request_id: str | None
    created_at: datetime


@router.get("/audit-logs", response_model=Ok[Page[AuditLogOut]])
def list_audit_logs(
    _: User = require_permission(Permission.AUDIT_VIEW),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    action: str | None = Query(default=None, max_length=64),
) -> Ok[Page[AuditLogOut]]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Ok(
        data=Page(
            items=[AuditLogOut.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )
