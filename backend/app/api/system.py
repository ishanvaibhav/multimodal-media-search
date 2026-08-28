"""System information, metrics, storage statistics, consistency.

In production these diagnostic endpoints require admin authentication; the
public health endpoints remain the only unauthenticated status surface.
"""
from __future__ import annotations

import platform

from fastapi import APIRouter, Depends, Query, Request

from .. import __version__
from ..infrastructure import metrics
from ..infrastructure.embedding import detect_resources
from ..schemas.admin import SystemInfo
from .deps import get_container, require_admin, require_admin_in_production

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info", response_model=SystemInfo, dependencies=[Depends(require_admin_in_production)])
async def system_info(request: Request):
    container = get_container(request)
    s = container.settings
    ffmpeg_version = container.ffmpeg.ffmpeg_version()
    ffprobe = container.ffmpeg.resolve_ffprobe()
    resources = detect_resources()
    return SystemInfo(
        app_name=s.app_name,
        app_env=s.app_env,
        version=__version__,
        python=platform.python_version(),
        embedding_backend=container.embedding.name,
        semantic_search=container.embedding.semantic,
        model=container.embedding.model_name,
        embedding_dim=container.embedding.dim,
        embedding_device=getattr(container.embedding, "device", "cpu"),
        ffmpeg=ffmpeg_version,
        ffprobe=str(ffprobe) if ffprobe else "unavailable",
        chroma_collection=s.chroma_collection,
        data_dir=str(s.data_dir_path),
        storage=container.storage.storage_stats(),
        resources=resources,
        admin_auth="token" if s.admin_token else ("required" if s.production else "open"),
        budgets={
            "search_p50_latency_budget_ms": s.search_p50_latency_budget_ms,
            "search_p95_latency_budget_ms": s.search_p95_latency_budget_ms,
            "fine_search_max_frames": s.fine_search_max_frames,
            "rerank_max_candidates": s.rerank_max_candidates,
            "llm_max_tokens": s.llm_max_tokens,
        },
    )


@router.get("/storage", dependencies=[Depends(require_admin_in_production)])
async def storage_stats(request: Request):
    return get_container(request).storage.storage_stats()


@router.get("/metrics", dependencies=[Depends(require_admin_in_production)])
async def get_metrics(request: Request):
    return metrics.snapshot()


@router.get("/consistency", dependencies=[Depends(require_admin_in_production)])
async def consistency(request: Request, repair: bool = Query(False)):
    if repair:
        # repair is destructive-ish; require full admin auth
        require_admin(request)
    return get_container(request).consistency_service.check(repair=repair)
