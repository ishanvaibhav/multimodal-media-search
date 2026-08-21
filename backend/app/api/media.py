"""Media library, frame/thumbnail serving and video streaming endpoints."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..exceptions import NotFoundError
from ..schemas.media import (
    MediaDeleteResponse,
    MediaListResponse,
    ReindexResponse,
    VideoDetail,
)
from ..utils import validate_id
from .deps import get_container

router = APIRouter(prefix="/api/media", tags=["media"])

_CHUNK = 1024 * 1024


@router.get("", response_model=MediaListResponse)
async def list_media(
    request: Request,
    search: str = "",
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = "uploaded_at",
    sort_order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    media_types: str | None = None,
    min_duration: float | None = Query(None, ge=0),
    max_duration: float | None = Query(None, ge=0),
    media_type: str | None = Query(None, description="video | image"),
    # keyset pagination (additive; no OFFSET for large datasets)
    after_key: str | None = Query(None, description="uploaded_at of the last row"),
    after_id: str | None = Query(None, description="video_id of the last row"),
):
    container = get_container(request)
    types = [t.strip().lower() for t in (media_types or "").split(",") if t.strip()] or None

    use_keyset = (
        after_key is not None and after_id is not None
        and not search and not date_from and not date_to
    )
    if use_keyset:
        # empty cursor strings mean "first page"
        cursor = (after_key, after_id) if (after_key and after_id) else None
        # keyset path (simple filters only)
        items = container.video_repo.list_keyset(
            after=cursor,
            limit=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            status=status,
        )
        total = container.video_repo.count(status=status, media_type=media_type)
        if media_type:
            items = [v for v in items if v.media_type == media_type]
        next_cursor = None
        if items:
            last = items[-1]
            next_cursor = {"after_key": last.uploaded_at or "", "after_id": last.video_id}
        return {
            "items": [container.media_service._serialize(v) for v in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "next_cursor": next_cursor,
        }

    return container.media_service.list(
        search=search, status=status, date_from=date_from, date_to=date_to,
        sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size,
        media_types=types, min_duration=min_duration, max_duration=max_duration,
        media_type=media_type,
    )


@router.get("/{video_id}", response_model=VideoDetail)
async def get_media(video_id: str, request: Request):
    validate_id(video_id, "video_id")
    return get_container(request).media_service.get(video_id)


@router.delete("/{video_id}", response_model=MediaDeleteResponse)
async def delete_media(video_id: str, request: Request):
    validate_id(video_id, "video_id")
    # delete performs a coordinated cancel-and-wait (threading waits) — run it
    # off the event loop so other async work stays responsive.
    container = get_container(request)
    return await asyncio.to_thread(container.media_service.delete, video_id)


@router.post("/{video_id}/reindex", response_model=ReindexResponse)
async def reindex_media(video_id: str, request: Request):
    validate_id(video_id, "video_id")
    container = get_container(request)
    return await asyncio.to_thread(container.media_service.reindex, video_id)


@router.get("/{video_id}/thumbnail")
async def get_thumbnail(video_id: str, request: Request):
    validate_id(video_id, "video_id")
    container = get_container(request)
    video = container.video_repo.get(video_id)
    if video is None:
        raise NotFoundError(f"video '{video_id}' not found")
    thumb = container.storage.thumbnail_path(video_id)
    if not thumb.exists():
        raise NotFoundError("thumbnail not available yet")
    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/{video_id}/frames/{frame_id}")
async def get_frame(video_id: str, frame_id: str, request: Request):
    validate_id(video_id, "video_id")
    validate_id(frame_id, "frame_id")
    container = get_container(request)
    frame = container.frame_repo.get(frame_id)
    if frame is None or frame.video_id != video_id:
        raise NotFoundError(f"frame '{frame_id}' not found")
    # Frame paths are stored relative to DATA_DIR (video frames under frames/,
    # image "frames" are the image files under media/) — resolve with
    # containment inside the data root.
    path = container.storage.resolve_in_data(frame.frame_path)
    if not path.exists():
        raise NotFoundError("frame file missing on disk")
    return FileResponse(path, media_type=_image_content_type(path.suffix))


@router.get("/{video_id}/stream")
async def stream_video(video_id: str, request: Request):
    """HTTP single-range streaming so the <video> tag can seek precisely.

    * No Range header -> 200 with the full file.
    * Valid single range -> 206 with Content-Range / Content-Length.
    * Malformed or unsatisfiable range -> 416 with ``Content-Range: bytes */size``.
    """
    validate_id(video_id, "video_id")
    container = get_container(request)
    video = container.video_repo.get(video_id)
    if video is None:
        raise NotFoundError(f"video '{video_id}' not found")
    path = container.storage.resolve_in(container.settings.media_dir, video.path)
    if not path.exists():
        raise NotFoundError("video file missing on disk")

    # images are served as a plain file (no range streaming semantics)
    if video.media_type == "image":
        return FileResponse(path, media_type=_image_content_type(path.suffix))

    file_size = path.stat().st_size

    def _unsatisfiable():
        return StreamingResponse(
            iter(()),
            status_code=416,
            media_type=_media_type(video.container, path.suffix),
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": "0",
            },
        )

    range_header = request.headers.get("range")
    start, end = 0, file_size - 1
    status_code = 200
    if range_header is not None:
        if file_size == 0:
            return _unsatisfiable()
        if "," in range_header:
            # only single ranges are supported; a multi-range request is not
            # satisfiable as a single range
            return _unsatisfiable()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not m:
            return _unsatisfiable()
        first, last = m.group(1), m.group(2)

        if first == "" and last == "":
            return _unsatisfiable()          # "bytes=-" is malformed
        if first == "":
            # suffix range: bytes=-N (last N bytes)
            suffix = int(last)
            if suffix <= 0:
                return _unsatisfiable()      # bytes=-0
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(first)
            if last == "":
                end = file_size - 1          # open-ended: bytes=N-
            else:
                end = int(last)
            if start > end or start >= file_size:
                return _unsatisfiable()      # bytes=100-50 / start past EOF
            end = min(end, file_size - 1)
        status_code = 206

    def iterfile():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(_CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    media_type = _media_type(video.container, path.suffix)
    return StreamingResponse(iterfile(), status_code=status_code, media_type=media_type, headers=headers)


def _media_type(container: str | None, suffix: str) -> str:
    suffix = suffix.lower()
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mkv":
        return "video/x-matroska"
    if suffix in (".avi",):
        return "video/x-msvideo"
    if suffix in (".mov",):
        return "video/quicktime"
    return "video/mp4"


def _image_content_type(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/jpeg"
