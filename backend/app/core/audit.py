"""Audit logging (master plan §42).

Every security-relevant or admin action writes an immutable row:
    actor, action, target, details, request_id, timestamp.

Keep action names UPPER_SNAKE — they are the queryable vocabulary
(LOGIN, USER_CREATED, ROLE_CHANGED, USER_DISABLED, MEDIA_DELETED, …).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import AuditLog, User
from .logging import get_request_id

log = logging.getLogger(__name__)


def record_audit(
    db: Session,
    *,
    actor: User | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        request_id=get_request_id(),
    )
    db.add(entry)
    if commit:
        db.commit()
    log.info(
        "audit action=%s actor=%s target=%s/%s", action, actor.email if actor else "-", target_type, target_id
    )
    return entry
