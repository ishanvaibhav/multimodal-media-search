"""User resolution & provisioning (master plan §6 flow #8–#10).

Flow:
    verify token → lookup local user
        • by uid            → found: use it
        • by email (PENDING)→ admin pre-provisioned record; bind uid, activate
        • nowhere           → bootstrap rules (first user / dev) or fail closed

Policy
------
* Deactivated users are rejected with 403 on every request.
* Unknown users in production: 403 ACCOUNT_NOT_PROVISIONED (fail closed).
* Unknown users in development: auto-provisioned as MEDIA_SEARCHER so local
  login flows work without ceremony. First user ever becomes ADMIN.
* ``BOOTSTRAP_ADMIN_EMAIL`` pins which email may claim the bootstrap admin
  seat (set it in production).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..db.models import Role, User, UserStatus
from .firebase import Identity

log = logging.getLogger(__name__)


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def resolve_user(db: Session, identity: Identity, settings: Settings) -> User:
    """Map a verified identity to a local user record (creating/binding as
    policy allows) and enforce account status."""

    user = db.scalar(select(User).where(User.uid == identity.uid))

    if user is None:
        # Admin pre-provisioned by email? Bind the verified uid on first login.
        user = db.scalar(select(User).where(User.email == identity.email))
        if user is not None:
            if user.uid is not None and user.uid != identity.uid:
                # Record already bound to another identity — never rebind.
                raise AuthError("ACCOUNT_CONFLICT", "account is bound to a different identity")
            user.uid = identity.uid
            if user.status_enum == UserStatus.PENDING:
                user.status = UserStatus.ACTIVE.value
            if identity.display_name and not user.display_name:
                user.display_name = identity.display_name
            log.info("bound firebase identity to provisioned user %s", user.email)
        else:
            user = _provision_new_user(db, identity, settings)

    if user.status_enum == UserStatus.DEACTIVATED:
        raise AuthError("ACCOUNT_DEACTIVATED", "this account has been deactivated")

    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user


def _provision_new_user(db: Session, identity: Identity, settings: Settings) -> User:
    first_user = _user_count(db) == 0
    bootstrap_match = (
        settings.BOOTSTRAP_ADMIN_EMAIL is not None
        and identity.email == settings.BOOTSTRAP_ADMIN_EMAIL.strip().lower()
    )

    if settings.is_production and not (first_user or bootstrap_match):
        raise AuthError(
            "ACCOUNT_NOT_PROVISIONED",
            "no account exists for this identity — contact an administrator",
        )

    role = Role.ADMIN if (first_user or bootstrap_match) else Role.MEDIA_SEARCHER
    user = User(
        uid=identity.uid,
        email=identity.email,
        display_name=identity.display_name,
        role=role.value,
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    log.info("provisioned user %s with role %s", user.email, role.value)
    return user
