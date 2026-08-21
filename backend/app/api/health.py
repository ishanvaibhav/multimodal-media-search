"""Health-check endpoints.

* ``GET /api/health/live``  — liveness (process up).
* ``GET /api/health/ready`` — readiness (critical dependencies usable; in
  production a semantic embedding model is REQUIRED).
* ``GET /api/health``       — full component status with details.
"""
from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..schemas.admin import HealthResponse
from .deps import get_container

router = APIRouter(prefix="/api", tags=["system"])


def _ok(value: bool) -> str:
    return "ok" if value else "unavailable"


def _details(container) -> dict:
    # Public health endpoint: minimal, non-sensitive information only.
    # Absolute filesystem paths / hosts / secrets are never exposed here.
    return {
        "python": platform.python_version(),
        "embedding_backend": container.embedding.name,
        "semantic_search": container.embedding.semantic,
        "model": container.embedding.model_name,
        "embedding_dim": container.embedding.dim,
        "embedding_device": getattr(container.embedding, "device", "cpu"),
        "vectors": container.vectorstore.count(),
        "model_mismatch": bool(getattr(container.vectorstore, "model_mismatch", False)),
        "maintenance": container.gate.active,
    }


@router.get("/health/live")
async def liveness(request: Request):
    return JSONResponse({"status": "ok"})


@router.get("/health/ready")
async def readiness(request: Request):
    container = get_container(request)
    s = container.settings

    db_ok = container.database.ping()
    chroma_ok = container.vectorstore.healthy()
    ffmpeg_ok = container.ffmpeg.ffmpeg_available()
    storage_ok = _storage_writable(s)

    # production readiness requires semantic embeddings + no model mismatch
    semantic_required = s.production
    model_ok = container.embedding is not None and not bool(
        getattr(container.vectorstore, "model_mismatch", False)
    )
    ready = all([db_ok, chroma_ok, ffmpeg_ok, storage_ok, model_ok])
    if semantic_required:
        ready = ready and container.embedding.semantic
    if container.gate.active:
        ready = False  # maintenance in progress => not ready for new work

    body = {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": _ok(db_ok),
            "chromadb": _ok(chroma_ok),
            "ffmpeg": _ok(ffmpeg_ok),
            "storage": _ok(storage_ok),
            "embedding_model": _ok(container.embedding is not None),
            "semantic_search": _ok(container.embedding.semantic),
            "model_mismatch": _ok(not bool(getattr(container.vectorstore, "model_mismatch", False))),
            "maintenance": _ok(not container.gate.active),
        },
    }
    return JSONResponse(body, status_code=200 if ready else 503)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    container = get_container(request)
    s = container.settings

    db_ok = container.database.ping()
    chroma_ok = container.vectorstore.healthy()
    ffmpeg_ok = container.ffmpeg.ffmpeg_available()
    ffprobe_ok = container.ffmpeg.ffprobe_available()
    model_ok = container.embedding is not None
    storage_ok = _storage_writable(s)
    worker_ok = container.worker.heartbeat_ok()

    return HealthResponse(
        api="ok",
        database=_ok(db_ok),
        chromadb=_ok(chroma_ok),
        ffmpeg=_ok(ffmpeg_ok),
        ffprobe=_ok(ffprobe_ok),
        embedding_model=_ok(model_ok),
        storage=_ok(storage_ok),
        worker=_ok(worker_ok),
        details=_details(container),
    )


def _storage_writable(s) -> bool:
    try:
        probe = s.data_dir_path / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False
