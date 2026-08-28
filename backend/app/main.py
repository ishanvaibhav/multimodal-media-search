"""FastAPI application entrypoint.

Run with:
    python -m uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import sys
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
<<<<<<< HEAD
from .api import admin, contexts, health, jobs, media, search, system, uploads
=======
from .api import admin, health, jobs, media, search, system, uploads
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
from .config import Settings, get_settings
from .container import build_container
from .exceptions import AppError
from .logging_config import configure_logging, get_logger

log = get_logger(__name__)

# contextvar for per-request id (structured logging)
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("INFO", settings.logs_dir)
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIDFilter())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = build_container(settings)
        app.state.container = container
        run_startup_validation(container)
        # crash/restart recovery + path portability migration (all idempotent)
        container.recovery_service.normalize_paths()
        recovered = container.recovery_service.recover_interrupted_jobs()
        container.recovery_service.clean_temp()
        container.recovery_service.cleanup_history()
        container.recovery_service.cleanup_stale_fine_cache()
        cleaned = container.upload_service.cleanup_abandoned()
        log.info(
            "Startup: recovered=%s cleaned_uploads=%d", recovered, cleaned
        )
        log.info(
            "Deployment model: single-process embedded worker (MAX_CONCURRENT_JOBS=%d)",
            settings.max_concurrent_jobs,
        )
        warn_unsupported_multi_worker()
        container.worker.start()
        log.info(
            "%s v%s ready on http://%s:%s",
            settings.app_name, __version__, settings.backend_host, settings.backend_port,
        )
        yield
        await container.worker.stop()
        container.vectorstore.close()
        container.database.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Temporal multimodal video search and retrieval platform",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    if settings.production:
        # explicit allowlist only — no regex, no localhost defaults, no wildcards
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins(settings),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-request-id"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins(settings),
            allow_origin_regex=r"https?://[a-zA-Z0-9.-]+\.e2b\.app",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-request-id"],
        )

    app.include_router(uploads.router)
    app.include_router(media.router)
    app.include_router(search.router)
<<<<<<< HEAD
    app.include_router(contexts.router)
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
    app.include_router(jobs.router)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(system.router)

    register_exception_handlers(app)
    return app


def _cors_origins(settings: Settings) -> list[str]:
    if settings.production:
        # explicit allowlist only (frontend_url + CORS_ORIGINS)
        origins = [settings.frontend_url]
        for extra in (settings.cors_origins or "").split(","):
            extra = extra.strip()
            if extra:
                origins.append(extra)
        return list(dict.fromkeys(origins))
    # development: permissive local origins + configured extras
    origins = [settings.frontend_url, "http://localhost:3000", "http://127.0.0.1:3000"]
    for extra in (settings.cors_origins or "").split(","):
        extra = extra.strip()
        if extra:
            origins.append(extra)
    return list(dict.fromkeys(origins))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.to_dict()},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "detail": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "an unexpected error occurred",
                    "detail": None,
                }
            },
        )


def warn_unsupported_multi_worker() -> None:
    """Single-process deployment guard.

    Per-video coordination, chunk locks, upload limits and the maintenance
    barrier are all process-local. Multi-worker/multi-process deployments are
    NOT supported and must not be silently allowed — warn loudly if the
    environment suggests one.
    """
    import os

    for var in ("WEB_CONCURRENCY", "WEB_WORKERS", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        value = os.environ.get(var)
        if value:
            try:
                n = int(value)
            except ValueError:
                continue
            if n > 1:
                log.error(
                    "%s=%d is set, but this revision supports a SINGLE embedded "
                    "worker process. Per-video coordination and maintenance are "
                    "process-local; running multiple worker processes would break "
                    "the delete-all/index coordination guarantees. Run with one "
                    "process (uvicorn default) or adopt a distributed worker "
                    "backend (Redis/Celery/RQ).",
                    var, n,
                )


def run_startup_validation(container) -> None:
    """Validate the environment and report clear, actionable messages."""
    settings = container.settings
    issues: list[str] = []

    if sys.version_info < (3, 10):
        issues.append(f"Python 3.10+ required (found {sys.version.split()[0]})")

    if not container.database.ping():
        issues.append("SQLite database is not accessible")
    if not container.vectorstore.healthy():
        issues.append("ChromaDB is not healthy")
    if not container.ffmpeg.ffmpeg_available():
        log.error(
            "FFmpeg NOT FOUND. Install FFmpeg (https://ffmpeg.org), set FFMPEG_PATH, "
            "or `pip install imageio-ffmpeg`. Indexing will fail until it is available."
        )
    else:
        log.info("FFmpeg: %s", container.ffmpeg.ffmpeg_version())
    if container.ffmpeg.ffprobe_available():
        log.info("FFprobe: %s", container.ffmpeg.resolve_ffprobe())
    else:
        log.warning("FFprobe not found; metadata will be parsed via `ffmpeg -i` fallback")

    # production mode requires semantic embeddings (fail closed)
    if settings.production and not container.embedding.semantic:
        issues.append(
            "APP_ENV=production requires a semantic embedding model "
            "(EMBEDDING_BACKEND=siglip or auto with the model available)"
        )

    if issues:
        raise RuntimeError("Startup validation failed:\n  - " + "\n  - ".join(issues))

    log.info("Data directory: %s", settings.data_dir_path)
    log.info(
        "Embedding backend: %s (dim=%d, semantic_search=%s)",
        container.embedding.name, container.embedding.dim, container.embedding.semantic,
    )
    log.info(
        "Vector store: chroma at %s (collection=%s)",
        settings.chroma_dir, settings.chroma_collection,
    )
    log.info("Startup validation complete.")


app = create_app()
