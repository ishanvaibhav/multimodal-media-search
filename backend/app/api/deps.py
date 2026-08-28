from __future__ import annotations

import secrets

from fastapi import Header, Request

from ..container import Container
from ..exceptions import AppError


def get_container(request: Request) -> Container:
    return request.app.state.container


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class AdminNotConfiguredError(AppError):
    status_code = 503
    code = "admin_not_configured"


def require_admin(request: Request):
    """Authorize destructive/admin operations (see ``authorize_admin``)."""
    container: Container = get_container(request)
    provided = (
        request.headers.get("x-admin-token")
        or _bearer_token(request.headers.get("authorization", ""))
        or ""
    )
    authorize_admin(container.settings, provided)
    return container


def authorize_admin(settings, provided: str) -> None:
    """Pure auth decision (unit-testable without booting the app).

    * production: an ADMIN_TOKEN MUST be configured and presented, otherwise
      the operation fails closed (503 if unconfigured, 401 on bad token).
    * development: if an ADMIN_TOKEN is configured it must be presented;
      otherwise local-only access is allowed.
    """
    token = settings.admin_token
    if settings.production:
        if not token:
            raise AdminNotConfiguredError(
                "ADMIN_TOKEN is not configured; destructive operations are "
                "disabled in production"
            )
        if not provided or not secrets.compare_digest(provided, token):
            raise UnauthorizedError("invalid admin token")
        return
    # development
    if token:
        if not provided or not secrets.compare_digest(provided, token):
            raise UnauthorizedError("invalid admin token")


def require_admin_in_production(request: Request):
    """Diagnostic/system endpoints: admin-only in production, open in dev."""
    container: Container = get_container(request)
    if container.settings.production:
        provided = (
            request.headers.get("x-admin-token")
            or _bearer_token(request.headers.get("authorization", ""))
            or ""
        )
        authorize_admin(container.settings, provided)
    return container


def _bearer_token(header: str) -> str:
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""
