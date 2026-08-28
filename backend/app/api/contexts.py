"""Saved contexts: save, list, delete, export."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..schemas.search import (
    ContextDeleteResponse,
    ContextSaveRequest,
    SavedContextList,
    SavedContextOut,
)
from ..utils import format_hms
from .deps import get_container

router = APIRouter(prefix="/api/contexts", tags=["contexts"])


@router.post("", response_model=SavedContextOut)
async def save_context(body: ContextSaveRequest, request: Request):
    svc = get_container(request).context_service
    ctx = svc.save(body.model_dump())
    return SavedContextOut(**ctx.to_dict(), timestamp_hms=format_hms(ctx.timestamp_seconds))


@router.get("", response_model=SavedContextList)
async def list_contexts(request: Request, limit: int = Query(100, ge=1, le=1000)):
    items = get_container(request).context_service.list(limit=limit)
    return SavedContextList(items=[
        SavedContextOut(**s.to_dict(), timestamp_hms=format_hms(s.timestamp_seconds))
        for s in items
    ])


@router.delete("/{ctx_id}", response_model=ContextDeleteResponse)
async def delete_context(ctx_id: int, request: Request):
    return ContextDeleteResponse(**get_container(request).context_service.delete(ctx_id))


@router.get("/export")
async def export_contexts(
    request: Request,
    format: str = Query("txt", pattern="^(txt|json|csv)$"),
    limit: int = Query(1000, ge=1, le=10000),
):
    result = get_container(request).context_service.export(fmt=format, limit=limit)
    return Response(
        content=result["content"],
        media_type=result["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="saved_contexts.{result["extension"]}"'
        },
    )
