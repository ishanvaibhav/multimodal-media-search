from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: Any = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class MessageResponse(BaseModel):
    message: str
    detail: Any = None


class JobOut(BaseModel):
    job_id: str
    video_id: str
    type: str
    status: str
    progress: float
    current_stage: str
    frames_processed: int
    frames_total: int
    frames_sampled: int = 0
    frames_kept: int = 0
    frames_embedded: int = 0
    retry_count: int = 0
    checkpoint: Optional[str] = None
    media_type: str = "video"
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
