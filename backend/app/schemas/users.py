"""User-facing API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..db.models import Role, UserStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    role: str
    status: str
    recovery_phone: str | None
    created_at: datetime
    last_login_at: datetime | None


class ProfileUpdateIn(BaseModel):
    """Self-service profile fields (§6). Password changes stay client-side
    through Firebase SDK — the backend never touches credentials."""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    recovery_phone: str | None = Field(default=None, max_length=40)


class UserCreateIn(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=200)
    role: Role = Role.MEDIA_SEARCHER


class UserRoleUpdateIn(BaseModel):
    role: Role


class UserStatusUpdateIn(BaseModel):
    status: UserStatus

    def validate_actionable(self) -> None:
        if self.status == UserStatus.PENDING:
            raise ValueError("status can only transition to ACTIVE or DEACTIVATED")


# Keep the enum importable for OpenAPI generation.
_ = (Role, UserStatus)
