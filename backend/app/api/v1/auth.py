"""Self-service identity endpoints (plan §44 — /api/auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...api.deps import get_current_user
from ...api.errors import Ok
from ...auth.permissions import permissions_for_role
from ...db.models import User
from ...db.session import get_db
from ...schemas.users import ProfileUpdateIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


class MeOut(UserOut):
    permissions: list[str]


@router.get("/me", response_model=Ok[MeOut])
def get_me(user: User = Depends(get_current_user)) -> Ok[MeOut]:
    """The bootstrap payload the frontend uses right after Firebase login:
    profile + role + the effective permission set (drives UI hiding only —
    every privileged endpoint re-checks server-side)."""
    data = MeOut(
        **UserOut.model_validate(user).model_dump(),
        permissions=sorted(p.value for p in permissions_for_role(user.role_enum)),
    )
    return Ok(data=data)


@router.patch("/profile", response_model=Ok[UserOut])
def update_profile(
    body: ProfileUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Ok[UserOut]:
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.recovery_phone is not None:
        user.recovery_phone = body.recovery_phone
    db.add(user)
    db.commit()
    db.refresh(user)
    return Ok(data=UserOut.model_validate(user))
