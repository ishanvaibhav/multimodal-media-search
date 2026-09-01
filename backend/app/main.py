"""Application factory (plan §44, §46, §47).

Run locally:
    cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.errors import register_exception_handlers
from .api.middleware import RequestContextMiddleware
from .api.v1.router import api_router
from .auth.firebase import init_verifier
from .core.config import get_settings
from .core.logging import configure_logging
from .db.models import SystemEvent, SystemEventType
from .db.session import get_engine, get_session_factory

log = logging.getLogger(__name__)


def _init_schema() -> None:
    """Ensure the schema exists.

    Development/test: ``create_all`` directly (fast, dependency-free).
    Production: migrations via Alembic are the supported path — we still call
    ``create_all`` harmlessly (it's a no-op when Alembic-managed tables
    exist) but log a reminder. ``alembic upgrade head`` runs in the
    container entrypoint / deploy pipeline.
    """
    from .db.base import Base

    Base.metadata.create_all(get_engine())


def _record_system_event(type_: SystemEventType, details: dict) -> None:
    try:
        db = get_session_factory()()
        try:
            db.add(SystemEvent(type=type_.value, details=details))
            db.commit()
        finally:
            db.close()
    except Exception:  # never let observability break startup
        log.exception("failed to record system event %s", type_.value)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        init_verifier(settings)
        _init_schema()
        _record_system_event(
            SystemEventType.STARTUP,
            {"app_env": settings.APP_ENV, "auth_mode": settings.AUTH_MODE, "version": __version__},
        )
        log.info(
            "ai-media-hub api started env=%s auth=%s version=%s",
            settings.APP_ENV,
            settings.AUTH_MODE,
            __version__,
        )
        yield
        _record_system_event(SystemEventType.SHUTDOWN, {})

    app = FastAPI(
        title="AI Media Hub API",
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
