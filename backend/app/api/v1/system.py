"""Health & observability endpoints (plan §47).

/health/live  — process is alive (no dependencies touched)
/health/ready — dependencies reachable (database today; Redis/Chroma later)
/health       — human-readable combined status
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ... import __version__
from ...core.config import Settings, get_settings
from ...db.session import get_db

router = APIRouter(tags=["system"])


class ComponentHealth(BaseModel):
    status: str  # "ok" | "degraded" | "down"
    detail: str | None = None


class HealthOut(BaseModel):
    status: str
    version: str
    app_env: str
    api_version: str
    components: dict[str, ComponentHealth]


def _check_database(db: Session) -> ComponentHealth:
    try:
        db.execute(text("SELECT 1"))
        return ComponentHealth(status="ok")
    except Exception as exc:  # noqa: BLE001 — health checks must never raise
        return ComponentHealth(status="down", detail=type(exc).__name__)


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> HealthOut:
    db_health = _check_database(db)
    components = {"database": db_health}
    overall = "ok" if all(c.status == "ok" for c in components.values()) else "degraded"
    return HealthOut(
        status=overall,
        version=__version__,
        app_env=settings.APP_ENV,
        api_version=settings.API_VERSION,
        components=components,
    )


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=HealthOut)
def readiness(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> HealthOut:
    return health(db=db, settings=settings)


class VersionOut(BaseModel):
    api_version: str
    db_schema_version: int
    preprocessing_version: int
    index_version: int
    embedding_version: int
    embedding_model: str


@router.get("/api/system/version", response_model=VersionOut)
def versions(settings: Settings = Depends(get_settings)) -> VersionOut:
    """Version surface for the fail-closed compatibility checks (plan §58)."""
    return VersionOut(
        api_version=settings.API_VERSION,
        db_schema_version=settings.DB_SCHEMA_VERSION,
        preprocessing_version=settings.PREPROCESSING_VERSION,
        index_version=settings.INDEX_VERSION,
        embedding_version=settings.EMBEDDING_VERSION,
        embedding_model=settings.EMBEDDING_MODEL,
    )
