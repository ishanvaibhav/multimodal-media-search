"""Domain models: enums and plain data classes shared by services/repositories."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class VideoStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    VALIDATING = "validating"
    PROBING = "probing"
    EXTRACTING_FRAMES = "extracting_frames"
    DEDUPLICATING = "deduplicating"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    FINALIZING = "finalizing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaType(str, enum.Enum):
    VIDEO = "video"
    IMAGE = "image"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class JobStage(str, enum.Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PROBING = "probing"
    EXTRACTING_FRAMES = "extracting_frames"
    DEDUPLICATING = "deduplicating"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    FINALIZING = "finalizing"


class UploadStatus(str, enum.Enum):
    UPLOADING = "uploading"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class JobType(str, enum.Enum):
    INDEX = "index"
    REINDEX = "reindex"


@dataclass
class MediaInfo:
    duration: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    container: str | None = None
    bitrate: int | None = None
    has_audio: bool = False
    creation_time: str | None = None


@dataclass
class Video:
    video_id: str
    filename: str
    original_filename: str
    path: str
    size_bytes: int = 0
    duration_seconds: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    container: str | None = None
    bitrate: int | None = None
    has_audio: bool = False
    creation_time: str | None = None
    media_type: str = MediaType.VIDEO.value
    status: str = VideoStatus.PENDING.value
    frame_count: int = 0
    upload_id: str | None = None
    error: str | None = None
    needs_reconciliation: int = 0
    uploaded_at: str | None = None
    indexed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Video":
        d = dict(row) if not isinstance(row, dict) else row
        return cls(
            video_id=d["video_id"],
            filename=d["filename"],
            original_filename=d["original_filename"],
            path=d["path"],
            size_bytes=d["size_bytes"],
            duration_seconds=d.get("duration_seconds"),
            fps=d.get("fps"),
            width=d.get("width"),
            height=d.get("height"),
            codec=d.get("codec"),
            container=d.get("container"),
            bitrate=d.get("bitrate"),
            has_audio=bool(d.get("has_audio")),
            creation_time=d.get("creation_time"),
            media_type=d.get("media_type") or MediaType.VIDEO.value,
            status=d["status"],
            frame_count=d.get("frame_count") or 0,
            upload_id=d.get("upload_id"),
            error=d.get("error"),
            needs_reconciliation=d.get("needs_reconciliation") or 0,
            uploaded_at=d.get("uploaded_at"),
            indexed_at=d.get("indexed_at"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "filename": self.filename,
            "original_filename": self.original_filename,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "container": self.container,
            "bitrate": self.bitrate,
            "has_audio": self.has_audio,
            "creation_time": self.creation_time,
            "media_type": self.media_type,
            "status": self.status,
            "frame_count": self.frame_count,
            "upload_id": self.upload_id,
            "error": self.error,
            "needs_reconciliation": self.needs_reconciliation,
            "uploaded_at": self.uploaded_at,
            "indexed_at": self.indexed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class FrameType(str, enum.Enum):
    COARSE = "coarse"
    FINE_CACHE = "fine_cache"


@dataclass
class Frame:
    frame_id: str
    video_id: str
    timestamp_seconds: float
    frame_path: str
    created_at: str | None = None
    frame_type: str = FrameType.COARSE.value

    @classmethod
    def from_row(cls, row: Any) -> "Frame":
        d = dict(row) if not isinstance(row, dict) else row
        return cls(
            frame_id=d["frame_id"],
            video_id=d["video_id"],
            timestamp_seconds=d["timestamp_seconds"],
            frame_path=d["frame_path"],
            created_at=d.get("created_at"),
            frame_type=d.get("frame_type") or FrameType.COARSE.value,
        )


@dataclass
class Upload:
    upload_id: str
    filename: str
    file_size: int
    content_type: str | None
    chunk_size: int
    total_chunks: int
    received_chunks: int = 0
    received_bytes: int = 0
    status: str = UploadStatus.UPLOADING.value
    error: str | None = None
    result_video_id: str | None = None
    result_job_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Upload":
        d = dict(row) if not isinstance(row, dict) else row
        return cls(
            upload_id=d["upload_id"],
            filename=d["filename"],
            file_size=d["file_size"],
            content_type=d.get("content_type"),
            chunk_size=d["chunk_size"],
            total_chunks=d["total_chunks"],
            received_chunks=d.get("received_chunks") or 0,
            received_bytes=d.get("received_bytes") or 0,
            status=d["status"],
            error=d.get("error"),
            result_video_id=d.get("result_video_id"),
            result_job_id=d.get("result_job_id"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "content_type": self.content_type,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "received_chunks": self.received_chunks,
            "received_bytes": self.received_bytes,
            "status": self.status,
            "error": self.error,
            "result_video_id": self.result_video_id,
            "result_job_id": self.result_job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Job:
    job_id: str
    video_id: str
    type: str = JobType.INDEX.value
    status: str = JobStatus.QUEUED.value
    progress: float = 0.0
    current_stage: str = JobStage.QUEUED.value
    frames_processed: int = 0
    frames_total: int = 0
    frames_sampled: int = 0
    frames_kept: int = 0
    frames_embedded: int = 0
    retry_count: int = 0
    checkpoint: str | None = None
    media_type: str = MediaType.VIDEO.value
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        d = dict(row) if not isinstance(row, dict) else row
        return cls(
            job_id=d["job_id"],
            video_id=d["video_id"],
            type=d.get("type") or JobType.INDEX.value,
            status=d["status"],
            progress=d.get("progress") or 0.0,
            current_stage=d.get("current_stage") or JobStage.QUEUED.value,
            frames_processed=d.get("frames_processed") or 0,
            frames_total=d.get("frames_total") or 0,
            frames_sampled=d.get("frames_sampled") or 0,
            frames_kept=d.get("frames_kept") or 0,
            frames_embedded=d.get("frames_embedded") or 0,
            retry_count=d.get("retry_count") or 0,
            checkpoint=d.get("checkpoint"),
            media_type=d.get("media_type") or MediaType.VIDEO.value,
            error=d.get("error"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "video_id": self.video_id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "current_stage": self.current_stage,
            "frames_processed": self.frames_processed,
            "frames_total": self.frames_total,
            "frames_sampled": self.frames_sampled,
            "frames_kept": self.frames_kept,
            "frames_embedded": self.frames_embedded,
            "retry_count": self.retry_count,
            "checkpoint": self.checkpoint,
            "media_type": self.media_type,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class FrameSample:
    """A single extracted frame file plus its media timestamp."""
    path: Path
    timestamp_seconds: float


@dataclass
<<<<<<< HEAD
class SavedContext:
    """A user-saved search result (query + media + timestamp + context)."""
    id: int | None = None
    query: str = ""
    video_id: str = ""
    filename: str = ""
    media_type: str = MediaType.VIDEO.value
    timestamp_seconds: float = 0.0
    context_start: float | None = None
    context_end: float | None = None
    score: float = 0.0
    frame_id: str | None = None
    context_text: str | None = None
    context_frames_json: str | None = None
    reason: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "SavedContext":
        d = dict(row) if not isinstance(row, dict) else row
        return cls(
            id=d.get("id"),
            query=d.get("query") or "",
            video_id=d.get("video_id") or "",
            filename=d.get("filename") or "",
            media_type=d.get("media_type") or MediaType.VIDEO.value,
            timestamp_seconds=d.get("timestamp_seconds") or 0.0,
            context_start=d.get("context_start"),
            context_end=d.get("context_end"),
            score=d.get("score") or 0.0,
            frame_id=d.get("frame_id"),
            context_text=d.get("context_text"),
            context_frames_json=d.get("context_frames_json"),
            reason=d.get("reason"),
            created_at=d.get("created_at"),
        )

    @property
    def context_frames(self) -> list:
        import json

        if not self.context_frames_json:
            return []
        try:
            return json.loads(self.context_frames_json)
        except (ValueError, TypeError):
            return []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "video_id": self.video_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "timestamp_seconds": self.timestamp_seconds,
            "context_start": self.context_start,
            "context_end": self.context_end,
            "score": self.score,
            "frame_id": self.frame_id,
            "context_text": self.context_text,
            "context_frames": self.context_frames,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
class Candidate:
    """A vector-store hit."""
    frame_id: str
    video_id: str
    timestamp_seconds: float
    score: float
    frame_path: str
    video_path: str
    uploaded_at: float | None = None
    duration: float | None = None
    metadata: dict = field(default_factory=dict)
<<<<<<< HEAD
    raw_score: float = 0.0     # pre-normalization cosine similarity
    final_score: float = 0.0   # deterministic rerank score (weighted signals)
    full_query_match: bool = False  # this candidate matched the FULL query embedding
=======
    raw_score: float = 0.0  # pre-normalization cosine similarity
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
