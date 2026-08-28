"""Resumable chunked upload endpoints.

Large files bypass the Next.js frontend entirely and talk directly to these
endpoints (see frontend/lib/uploads.ts).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request

from ..application.upload_service import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    UploadService,
)
from ..schemas.upload import (
    UploadAbortResponse,
    UploadChunkResponse,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadStatusResponse,
)
from ..utils import validate_id
from .deps import get_container

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _svc(request: Request) -> UploadService:
    return get_container(request).upload_service


@router.get("/config")
async def upload_config(request: Request):
    """Upload protocol configuration (chunk size, size cap, supported types).

    Lets the frontend agree on the SAME chunk protocol as the backend instead
    of hardcoding a chunk size that may exceed a proxy/server request limit.
    """
    container = get_container(request)
    s = container.settings
    return {
        "chunk_size": s.chunk_size_bytes,
        "chunk_size_mb": s.chunk_size_mb,
        "max_upload_size_bytes": s.max_upload_size_bytes,
        "max_upload_size_gb": s.max_upload_size_gb,
        "video_extensions": sorted(VIDEO_EXTENSIONS),
        "image_extensions": sorted(IMAGE_EXTENSIONS),
    }


@router.post("/init", response_model=UploadInitResponse)
async def init_upload(body: UploadInitRequest, request: Request):
    svc = _svc(request)
    upload = await asyncio.to_thread(
        svc.init, body.filename, body.file_size, body.content_type, body.chunk_size
    )
    return UploadInitResponse(
        upload_id=upload.upload_id,
        filename=upload.filename,
        file_size=upload.file_size,
        chunk_size=upload.chunk_size,
        total_chunks=upload.total_chunks,
        status=upload.status,
    )


@router.post("/{upload_id}/chunk", response_model=UploadChunkResponse)
async def upload_chunk(
    upload_id: str,
    request: Request,
    index: int = Query(..., ge=0, description="zero-based chunk index"),
):
    validate_id(upload_id, "upload_id")
    result = await _svc(request).receive_chunk(upload_id, index, request.stream())
    return UploadChunkResponse(**result, chunk_index=index)


@router.post("/{upload_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(upload_id: str, request: Request):
    validate_id(upload_id, "upload_id")
    # completion assembles chunks + runs ffprobe (subprocess) — off the loop.
    result = await asyncio.to_thread(_svc(request).complete, upload_id)
    return UploadCompleteResponse(**result)


@router.get("/{upload_id}/status", response_model=UploadStatusResponse)
async def upload_status(upload_id: str, request: Request):
    validate_id(upload_id, "upload_id")
    result = await asyncio.to_thread(_svc(request).status, upload_id)
    return UploadStatusResponse(**result)


@router.delete("/{upload_id}", response_model=UploadAbortResponse)
async def abort_upload(upload_id: str, request: Request):
    validate_id(upload_id, "upload_id")
    result = await asyncio.to_thread(_svc(request).abort, upload_id)
    return UploadAbortResponse(**result)
