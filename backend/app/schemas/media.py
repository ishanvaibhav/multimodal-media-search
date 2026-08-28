from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class VideoOut(BaseModel):
    video_id: str
    filename: str
    stored_filename: str
    media_type: str = "video"
    size_bytes: int
    duration_seconds: Optional[float] = None
    duration_hms: Optional[str] = None
    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codec: Optional[str] = None
    container: Optional[str] = None
    has_audio: bool = False
    status: str
    frame_count: int = 0
    error: Optional[str] = None
    uploaded_at: Optional[str] = None
    indexed_at: Optional[str] = None
    thumbnail_url: str
    stream_url: str


class FrameOut(BaseModel):
    frame_id: str
    timestamp: float
    timestamp_hms: str
    frame_url: str


class VideoDetail(VideoOut):
    frames: list[FrameOut] = []
    job: Optional[dict] = None


class MediaListResponse(BaseModel):
    items: list[VideoOut]
    total: int
    page: int
    page_size: int


class MediaDeleteResponse(BaseModel):
    video_id: str
    deleted: dict


class ReindexResponse(BaseModel):
    video_id: str
    job_id: str
