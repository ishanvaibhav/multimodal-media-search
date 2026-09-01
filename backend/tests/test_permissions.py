"""RBAC matrix unit tests (plan §7, §54 security tests)."""

from app.auth.permissions import Permission, role_allows
from app.db.models import Role


def test_admin_can_do_everything():
    for perm in Permission:
        assert role_allows(Role.ADMIN, perm), perm


def test_video_editor_matrix():
    allowed = {
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
    for perm in Permission:
        assert role_allows(Role.VIDEO_EDITOR, perm) == (perm in allowed), perm


def test_media_searcher_matrix():
    allowed = {
        Permission.MEDIA_VIEW,
        Permission.MEDIA_DOWNLOAD,
        Permission.SEARCH_EXECUTE,
        Permission.SEARCH_FEEDBACK,
        Permission.CONTEXT_CREATE,
        Permission.CONTEXT_DELETE,
        Permission.CONTEXT_EXPORT,
    }
    for perm in Permission:
        assert role_allows(Role.MEDIA_SEARCHER, perm) == (perm in allowed), perm


def test_restricted_powers_are_admin_only():
    for perm in (
        Permission.MEDIA_DELETE,
        Permission.MEDIA_REINDEX,
        Permission.USER_VIEW,
        Permission.USER_CREATE,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.SYSTEM_VIEW,
        Permission.SYSTEM_MAINTENANCE,
        Permission.SYSTEM_REPAIR,
        Permission.SYSTEM_CLEAR_DATA,
        Permission.AUDIT_VIEW,
    ):
        assert not role_allows(Role.VIDEO_EDITOR, perm), perm
        assert not role_allows(Role.MEDIA_SEARCHER, perm), perm
