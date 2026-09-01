"""Relational schema — AI Media Hub (master plan §11–§18).

SQLAlchemy 2.0 typed mappings. Status values are explicit string enums so
state machines (media lifecycle §59, job state machine §34) are auditable in
the database itself.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    VIDEO_EDITOR = "VIDEO_EDITOR"
    MEDIA_SEARCHER = "MEDIA_SEARCHER"


class UserStatus(str, enum.Enum):
    PENDING = "PENDING"  # provisioned by admin, awaiting first login
    ACTIVE = "ACTIVE"
    DEACTIVATED = "DEACTIVATED"


class MediaType(str, enum.Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class MediaStatus(str, enum.Enum):
    UPLOADING = "UPLOADING"
    REGISTERED = "REGISTERED"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class IndexStatus(str, enum.Enum):
    NOT_INDEXED = "NOT_INDEXED"
    QUEUED = "QUEUED"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class FrameType(str, enum.Enum):
    COARSE = "COARSE"
    FINE = "FINE"
    KEYFRAME = "KEYFRAME"
    THUMBNAIL = "THUMBNAIL"


class JobType(str, enum.Enum):
    UPLOAD = "UPLOAD"
    INDEX = "INDEX"
    REINDEX = "REINDEX"
    DELETE = "DELETE"
    FINE_SEARCH = "FINE_SEARCH"
    MAINTENANCE = "MAINTENANCE"
    REPAIR = "REPAIR"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Legal job state transitions (plan §34 — "never allow arbitrary transitions").
JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.CANCELLING, JobStatus.COMPLETED, JobStatus.FAILED}),
    JobStatus.CANCELLING: frozenset({JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class UploadStatus(str, enum.Enum):
    INITIATED = "INITIATED"
    RECEIVING = "RECEIVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SystemEventType(str, enum.Enum):
    MAINTENANCE_STARTED = "MAINTENANCE_STARTED"
    MAINTENANCE_STOPPED = "MAINTENANCE_STOPPED"
    DATA_CLEARED = "DATA_CLEARED"
    CONSISTENCY_SCAN = "CONSISTENCY_SCAN"
    RECOVERY = "RECOVERY"
    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"


# ---------------------------------------------------------------------------
# Users & access (plan §6)
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Firebase UID — NULL until a provisioned user logs in for the first time
    # and the verified token binds the identity to this record.
    uid: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default=Role.MEDIA_SEARCHER.value)
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.PENDING.value)
    recovery_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def status_enum(self) -> UserStatus:
        return UserStatus(self.status)


# ---------------------------------------------------------------------------
# Media (plan §12) — phases 3+
# ---------------------------------------------------------------------------


class Media(Base):
    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(1024))
    thumbnail_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=MediaStatus.REGISTERED.value, index=True)
    index_status: Mapped[str] = mapped_column(String(24), default=IndexStatus.NOT_INDEXED.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    frames: Mapped[list[Frame]] = relationship(back_populates="media", cascade="all, delete-orphan")


class MediaFile(Base):
    """Derivative files belonging to a media item (previews, proxies…)."""

    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # original | preview | proxy
    storage_key: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Frame(Base):
    """An indexed frame (plan §13). The embedding itself lives in ChromaDB —
    ``embedding_id`` is the cross-reference key used by the consistency
    engine (plan §38) to detect orphan vectors."""

    __tablename__ = "frames"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    frame_type: Mapped[str] = mapped_column(String(16), default=FrameType.COARSE.value)
    timestamp: Mapped[float] = mapped_column(Float)  # seconds into media
    frame_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    media: Mapped[Media] = relationship(back_populates="frames")

    __table_args__ = (Index("ix_frames_media_timestamp", "media_id", "timestamp"),)


# ---------------------------------------------------------------------------
# Jobs (plan §34–§35)
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.QUEUED.value, index=True)
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media.id"), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    # Progress counters (frames_sampled / kept / embedded…) and crash-recovery
    # checkpoint (plan §37): {"stage": ..., "last_frame": ..., "last_timestamp": ...}
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.created_at"
    )


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[Job] = relationship(back_populates="events")


# ---------------------------------------------------------------------------
# Chunked uploads (plan §9)
# ---------------------------------------------------------------------------


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    declared_size: Mapped[int] = mapped_column(Integer)
    chunk_size: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    received_chunks: Mapped[int] = mapped_column(Integer, default=0)
    received_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)  # client-declared
    status: Mapped[str] = mapped_column(String(24), default=UploadStatus.INITIATED.value, index=True)
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media.id"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list[UploadChunk]] = relationship(back_populates="upload", cascade="all, delete-orphan")


class UploadChunk(Base):
    __tablename__ = "upload_chunks"
    __table_args__ = (UniqueConstraint("upload_id", "index", name="upload_chunk"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    upload: Mapped[Upload] = relationship(back_populates="chunks")


# ---------------------------------------------------------------------------
# Search: history & feedback (plan §32–§33)
# ---------------------------------------------------------------------------


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16), default="fast")  # fast | accurate | metadata
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SearchFeedback(Base):
    __tablename__ = "search_feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    result_id: Mapped[str] = mapped_column(String(64))
    media_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevant: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedContext(Base):
    """A saved evidence moment (plan §30)."""

    __tablename__ = "saved_contexts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_frames: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Fine search cache (plan §22)
# ---------------------------------------------------------------------------


class FineSearchCache(Base):
    __tablename__ = "fine_search_cache"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    interval_start: Mapped[float] = mapped_column(Float)
    interval_end: Mapped[float] = mapped_column(Float)
    frame_interval: Mapped[float] = mapped_column(Float)
    embedding_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_fine_cache_interval", "media_id", "interval_start", "interval_end"),)


class FineSearchInterval(Base):
    """Mutable marker of in-progress fine-search ranges (crash recovery)."""

    __tablename__ = "fine_search_intervals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id"), index=True)
    interval_start: Mapped[float] = mapped_column(Float)
    interval_end: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Model registry (plan §18) — never silently mix embedding models
# ---------------------------------------------------------------------------


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    model_name: Mapped[str] = mapped_column(String(200))
    revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dimension: Mapped[int] = mapped_column(Integer)
    preprocessing_version: Mapped[int] = mapped_column(Integer)
    indexing_version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "model_name",
            "revision",
            "preprocessing_version",
            "indexing_version",
            name="model_version",
        ),
    )


# ---------------------------------------------------------------------------
# Audit & system events (plan §42, §38–§39)
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(48), index=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
