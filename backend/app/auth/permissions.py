"""RBAC permission layer (master plan §7).

Rules:
* No ``if role == ...`` checks scattered through the codebase — endpoints
  declare the *permission* they need, the mapping lives here alone.
* Backend enforcement only; frontend hiding is a UX nicety, never security
  (plan §70 — rule 1).
"""

from __future__ import annotations

import enum

from ..db.models import Role


class Permission(str, enum.Enum):
    # Media
    MEDIA_VIEW = "media.view"
    MEDIA_UPLOAD = "media.upload"
    MEDIA_DELETE = "media.delete"
    MEDIA_REINDEX = "media.reindex"
    MEDIA_DOWNLOAD = "media.download"

    # Search
    SEARCH_EXECUTE = "search.execute"
    SEARCH_FEEDBACK = "search.feedback"

    # Contexts
    CONTEXT_CREATE = "context.create"
    CONTEXT_DELETE = "context.delete"
    CONTEXT_EXPORT = "context.export"

    # Jobs
    JOB_VIEW = "job.view"
    JOB_CANCEL = "job.cancel"

    # Users
    USER_VIEW = "user.view"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_DELETE = "user.delete"

    # System
    SYSTEM_VIEW = "system.view"
    SYSTEM_MAINTENANCE = "system.maintenance"
    SYSTEM_REPAIR = "system.repair"
    SYSTEM_CLEAR_DATA = "system.clear_data"
    AUDIT_VIEW = "audit.view"


_ALL: frozenset[Permission] = frozenset(Permission)

_EDITOR: frozenset[Permission] = frozenset(
    {
        Permission.MEDIA_VIEW,
        Permission.MEDIA_UPLOAD,
        Permission.MEDIA_DOWNLOAD,
        Permission.SEARCH_EXECUTE,
        Permission.SEARCH_FEEDBACK,
        Permission.CONTEXT_CREATE,
        Permission.CONTEXT_DELETE,
        Permission.CONTEXT_EXPORT,
        Permission.JOB_VIEW,
        Permission.JOB_CANCEL,
    }
)

_SEARCHER: frozenset[Permission] = frozenset(
    {
        Permission.MEDIA_VIEW,
        Permission.MEDIA_DOWNLOAD,
        Permission.SEARCH_EXECUTE,
        Permission.SEARCH_FEEDBACK,
        Permission.CONTEXT_CREATE,
        Permission.CONTEXT_DELETE,
        Permission.CONTEXT_EXPORT,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: _ALL,
    Role.VIDEO_EDITOR: _EDITOR,
    Role.MEDIA_SEARCHER: _SEARCHER,
}


def permissions_for_role(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def role_allows(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
