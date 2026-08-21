from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class UploadInitRequest(BaseModel):
    filename: str
    file_size: int
    content_type: Optional[str] = None
    chunk_size: Optional[int] = None


class UploadInitResponse(BaseModel):
    upload_id: str
    filename: str
    file_size: int
    chunk_size: int
    total_chunks: int
    status: str


class UploadStatusResponse(BaseModel):
    upload_id: str
    filename: str
    file_size: int
    content_type: Optional[str] = None
    chunk_size: int
    total_chunks: int
    received_chunks: int
    received_bytes: int
    status: str
    error: Optional[str] = None
    progress: float
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class UploadChunkResponse(UploadStatusResponse):
    chunk_index: int = Field(description="index of the chunk just uploaded")


class UploadCompleteResponse(BaseModel):
    upload_id: str
    video_id: str
    job_id: str
    status: str


class UploadAbortResponse(BaseModel):
    upload_id: str
    status: str
