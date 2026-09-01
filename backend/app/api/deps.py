"""Shared FastAPI dependencies — DB session, identity, RBAC guards."""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..auth.firebase import TokenVerificationError, get_verifier
from ..auth.permissions import Permission, role_allows
from ..auth.service import resolve_user
from ..core.config import Settings, get_settings
from ..db.models import User
from ..db.session import get_db
from .errors import AppError

_bearer = HTTPBearer(auto_error=False)

SettingsDep = Depends(get_settings)
DbDep = Depends(get_db)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Authenticate the request and resolve the local user (plan §6)."""
    if credentials is None or not credentials.credentials:
        raise AppError.unauthenticated("missing bearer token")
    try:
        identity = get_verifier().verify(credentials.credentials)
    except TokenVerificationError as exc:
        raise AppError.unauthenticated("invalid authentication credentials") from exc
    return resolve_user(db, identity, settings)


CurrentUser = Depends(get_current_user)


def require_permission(permission: Permission):
    """RBAC guard factory (plan §7) — backend enforcement, always.

    Returns a ``Depends`` wrapper so it can be used directly as a parameter
    default: ``user: User = require_permission(Permission.USER_VIEW)``.
    """

    def _guard(user: User = Depends(get_current_user)) -> User:
        if not role_allows(user.role_enum, permission):
            raise AppError.forbidden(f"missing permission: {permission.value}")
        return user

    return Depends(_guard)
