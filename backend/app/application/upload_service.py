"""Resumable, chunked upload use-case.

* Chunks are streamed to disk (never buffered whole in memory) and tracked in
  SQLite. Each chunk write is serialised by a per-(upload, index) lock and is
  idempotent: re-uploading the same bytes succeeds; different bytes yield a
  409 Conflict. Files are written to a unique temp path, hashed, and atomically
  renamed into place.
* Completion uses an atomic CAS transition ``UPLOADING → COMPLETING →
  COMPLETED`` so concurrent / repeated completion requests produce exactly one
  video and one indexing job.
* Concurrent uploads are limited by an in-process semaphore (race-safe for the
  single-process deployment model).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterable, Optional

from ..config import Settings
from ..domain.models import Upload, Video, VideoStatus
from ..exceptions import (
    ConflictError,
    MediaProcessingError,
    NotFoundError,
    StorageError,
    UploadError,
    ValidationError,
)
from ..infrastructure import metrics
from ..infrastructure.coordinator import ChunkLocks, MaintenanceGate, UploadLimiter
from ..infrastructure.ffmpeg import FFmpegService
from ..infrastructure.repositories import UploadRepository, VideoRepository
from ..infrastructure.storage import StorageService
from ..logging_config import get_logger
from ..utils import now_iso, sanitize_filename, validate_id

log = get_logger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".flv", ".wmv",
}

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
}

SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS


def media_kind_from_name(filename: str) -> str:
    """Classify a sanitized filename as 'video' or 'image' by extension."""
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "video"


class _ImageInfo:
    """Shape-compatible stand-in for MediaInfo (images have no duration/fps/codec)."""
    duration = None
    fps = None
    codec = None
    bitrate = None
    has_audio = False
    creation_time = None

    def __init__(self, width: int, height: int, container: str):
        self.width = width
        self.height = height
        self.container = container


class UploadService:
    def __init__(
        self,
        settings: Settings,
        upload_repo: UploadRepository,
        video_repo: VideoRepository,
        storage: StorageService,
        ffmpeg: FFmpegService,
        job_service,
        chunk_locks: ChunkLocks | None = None,
        limiter: UploadLimiter | None = None,
        gate: MaintenanceGate | None = None,
    ):
        self.settings = settings
        self.uploads = upload_repo
        self.videos = video_repo
        self.storage = storage
        self.ffmpeg = ffmpeg
        self.jobs = job_service
        self.chunk_locks = chunk_locks or ChunkLocks()
        self.limiter = limiter
        self.gate = gate

    # ------------------------------------------------------------------
    def init(
        self,
        filename: str,
        file_size: int,
        content_type: str | None = None,
        chunk_size: int | None = None,
    ) -> Upload:
        if self.gate is not None:
            self.gate.require_not_active("upload")
        safe_name = sanitize_filename(filename, fallback="media.bin")
        ext = Path(safe_name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValidationError(
                f"Unsupported file type '{ext or '(none)'}'. Supported videos: "
                + ", ".join(sorted(VIDEO_EXTENSIONS))
                + ". Supported images: "
                + ", ".join(sorted(IMAGE_EXTENSIONS))
            )

        if file_size <= 0:
            raise ValidationError("file_size must be greater than zero")
        max_bytes = self.settings.max_upload_size_bytes
        if file_size > max_bytes:
            raise UploadError(
                f"File is {file_size} bytes but MAX_UPLOAD_SIZE_GB="
                f"{self.settings.max_upload_size_gb} allows at most {max_bytes} bytes"
            )

        if not self.storage.has_free_space(file_size):
            raise StorageError("Not enough free disk space for this upload")

        if chunk_size is None or chunk_size <= 0:
            chunk_size = self.settings.chunk_size_bytes
        chunk_size = max(64 * 1024, min(chunk_size, max_bytes))

        total_chunks = (file_size + chunk_size - 1) // chunk_size
        upload_id = uuid.uuid4().hex
        now = now_iso()

        # race-safe concurrency limit: reserve BEFORE inserting
        if self.limiter is not None:
            self.limiter.acquire(upload_id)

        try:
            upload = Upload(
                upload_id=upload_id,
                filename=safe_name,
                file_size=file_size,
                content_type=content_type,
                chunk_size=chunk_size,
                total_chunks=total_chunks,
                created_at=now,
                updated_at=now,
            )
            self.uploads.insert(upload)
            self.storage.upload_dir(upload_id)
        except Exception:
            if self.limiter is not None:
                self.limiter.release(upload_id)
            raise
        metrics.inc("uploads.started")
        metrics.set_gauge("uploads.active", float(self._active_count()))
        log.info(
            "UPLOAD START upload=%s file=%s size=%d chunks=%d",
            upload_id, safe_name, file_size, total_chunks,
        )
        return upload

    def _active_count(self) -> int:
        if self.limiter is not None:
            return self.limiter.active_count
        return self.uploads.count_active()

    # ------------------------------------------------------------------
    def _validate_image(self, path: Path) -> tuple[int, int, str]:
        """Validate an uploaded image: decodable, bounded dimensions.

        Guards against malformed files and decompression bombs (a tiny file
        claiming a huge pixel size) by checking dimensions BEFORE full decode.
        """
        from PIL import Image

        try:
            with Image.open(path) as im:
                im.verify()  # catches truncation / corruption
        except Exception as exc:
            raise UploadError(
                f"Uploaded file is not a decodable image: {exc.__class__.__name__}"
            ) from exc

        try:
            with Image.open(path) as im:
                width, height = im.size
                if width <= 0 or height <= 0:
                    raise UploadError("image has invalid dimensions")
                if width > self.settings.max_image_dimension or height > self.settings.max_image_dimension:
                    raise UploadError(
                        f"image dimension {width}x{height} exceeds "
                        f"MAX_IMAGE_DIMENSION={self.settings.max_image_dimension}"
                    )
                if width * height > self.settings.max_image_pixels:
                    raise UploadError(
                        f"image {width}x{height} exceeds MAX_IMAGE_PIXELS="
                        f"{self.settings.max_image_pixels} (possible decompression bomb)"
                    )
                fmt = (im.format or path.suffix.lstrip(".")).lower()
        except UploadError:
            raise
        except Exception as exc:
            raise UploadError(f"could not read image: {exc}") from exc
        return width, height, fmt

    # ------------------------------------------------------------------
    async def receive_chunk(
        self, upload_id: str, index: int, body: AsyncIterable[bytes]
    ) -> dict:
        validate_id(upload_id, "upload_id")
        upload = self.uploads.get(upload_id)
        if upload is None:
            raise NotFoundError(f"upload '{upload_id}' not found")
        if upload.status != "uploading":
            raise ConflictError(f"upload '{upload_id}' is not accepting chunks (status={upload.status})")
        if index < 0 or index >= upload.total_chunks:
            raise UploadError(f"chunk index {index} out of range [0, {upload.total_chunks})")

        # 1) stream to a unique temp file (never collide with other writers)
        tmp = self.storage.chunk_path(upload_id, index).with_suffix(f".part.{uuid.uuid4().hex}")
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as out:
                async for piece in body:
                    if not piece:
                        continue
                    size += len(piece)
                    if size > upload.chunk_size:
                        raise UploadError(f"chunk {index} exceeds chunk_size {upload.chunk_size}")
                    digest.update(piece)
                    out.write(piece)
                out.flush()
        except UploadError:
            tmp.unlink(missing_ok=True)
            raise
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"failed to write chunk {index}: {exc}") from exc

        if size == 0 and index < upload.total_chunks - 1:
            tmp.unlink(missing_ok=True)
            raise UploadError("empty chunk body")

        final_digest = digest.hexdigest()

        # 2) atomically claim the chunk slot under the per-chunk lock
        dest = self.storage.chunk_path(upload_id, index)
        with self.chunk_locks.hold(upload_id, index):
            existing = self.uploads.get_chunk(upload_id, index)
            if existing is not None:
                # idempotent re-upload with identical bytes → success
                if existing.get("sha256") == final_digest:
                    tmp.unlink(missing_ok=True)
                else:
                    tmp.unlink(missing_ok=True)
                    raise ConflictError(
                        f"chunk {index} already uploaded with a different content hash"
                    )
            else:
                if dest.exists():
                    # a previous writer wrote the file but the DB row is gone;
                    # only accept it if the bytes match
                    on_disk = hashlib.sha256(dest.read_bytes()).hexdigest()
                    if on_disk == final_digest:
                        tmp.unlink(missing_ok=True)
                    else:
                        tmp.unlink(missing_ok=True)
                        raise ConflictError(
                            f"chunk {index} exists on disk with a different content hash"
                        )
                else:
                    tmp.replace(dest)  # atomic within the lock
                self.uploads.mark_chunk(upload_id, index, size, final_digest)

        received = self.uploads.received_chunk_indices(upload_id)
        chunks = self.uploads.list_chunks(upload_id)
        received_bytes = sum(int(c["size_bytes"]) for c in chunks)
        self.uploads.update(
            upload_id,
            received_chunks=len(received),
            received_bytes=received_bytes,
        )
        return self._status_dict(upload, len(received), received_bytes)

    # ------------------------------------------------------------------
    def complete(self, upload_id: str) -> dict:
        validate_id(upload_id, "upload_id")
        upload = self.uploads.get(upload_id)
        if upload is None:
            raise NotFoundError(f"upload '{upload_id}' not found")

        # already completed → return the stored result (idempotent)
        if upload.status == "completed" and upload.result_video_id:
            return {
                "upload_id": upload_id,
                "video_id": upload.result_video_id,
                "job_id": upload.result_job_id,
                "status": "completed",
            }
        # completion in progress elsewhere → report in-progress
        if upload.status == "completing":
            return {"upload_id": upload_id, "status": "completing"}

        # atomic claim: only one request may move uploading|failed -> completing
        if not self.uploads.transition(upload_id, upload.status, "completing"):
            # lost the race; re-read and answer accordingly
            return self.complete(upload_id)

        try:
            result = self._do_complete(upload)
        except Exception as exc:
            # allow a safe retry: mark failed with the error
            self.uploads.update(upload_id, status="failed", error=str(exc))
            metrics.inc("uploads.failed")
            if self.limiter is not None:
                self.limiter.release(upload_id)
            if isinstance(exc, MediaProcessingError):
                raise UploadError(f"Uploaded file is not a valid video: {exc.message}") from exc
            raise

        metrics.inc("uploads.completed")
        metrics.set_gauge("uploads.active", float(self._active_count()))
        return result

    def _do_complete(self, upload: Upload) -> dict:
        if self.gate is not None:
            self.gate.require_not_active("upload completion")

        received = self.uploads.received_chunk_indices(upload.upload_id)
        missing = [i for i in range(upload.total_chunks) if i not in received]
        if missing:
            raise ConflictError(
                f"upload incomplete: {len(missing)} chunk(s) missing "
                f"(first missing: {missing[:5]})"
            )
        chunks = self.uploads.list_chunks(upload.upload_id)
        total_bytes = sum(int(c["size_bytes"]) for c in chunks)
        if total_bytes != upload.file_size:
            raise UploadError(
                f"byte count mismatch: expected {upload.file_size}, got {total_bytes}"
            )

        video_id = uuid.uuid4().hex
        ext = Path(upload.filename).suffix.lower() or ".mp4"
        dest_name = f"{video_id}{ext}"
        media_kind = media_kind_from_name(upload.filename)

        final_path: Optional[Path] = None
        try:
            final_path = self.storage.assemble_upload(
                upload.upload_id, dest_name, upload.total_chunks
            )
            if media_kind == "image":
                width, height, fmt = self._validate_image(final_path)
                info = _ImageInfo(width=width, height=height, container=fmt)
            else:
                info = self.ffmpeg.probe(final_path)
        except UploadError:
            self.storage.delete_upload_dir(upload.upload_id)
            if final_path is not None:
                final_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            self.storage.delete_upload_dir(upload.upload_id)
            if final_path is not None:
                final_path.unlink(missing_ok=True)  # never leave orphan media
            if isinstance(exc, MediaProcessingError):
                raise
            raise StorageError(f"failed to assemble upload: {exc}") from exc

        now = now_iso()
        video = Video(
            video_id=video_id,
            filename=dest_name,
            original_filename=upload.filename,
            path=self.storage.to_stored_path(final_path),
            size_bytes=total_bytes,
            duration_seconds=info.duration,
            fps=info.fps,
            width=info.width,
            height=info.height,
            codec=info.codec,
            container=info.container,
            bitrate=info.bitrate,
            has_audio=info.has_audio,
            creation_time=info.creation_time,
            media_type=media_kind,
            status=VideoStatus.QUEUED.value,
            upload_id=upload.upload_id,
            uploaded_at=now,
            created_at=now,
            updated_at=now,
        )
        self.videos.insert(video)
        job_id = self.jobs.create_index_job(video_id, type="index", media_type=media_kind)

        # free upload scratch space; record the result for idempotent replays
        self.storage.delete_upload_dir(upload.upload_id)
        self.uploads.update(
            upload.upload_id, status="completed",
            result_video_id=video_id, result_job_id=job_id, error=None,
        )
        if self.limiter is not None:
            self.limiter.release(upload.upload_id)
        log.info(
            "UPLOAD COMPLETE upload=%s video=%s size=%d job=%s",
            upload.upload_id, video_id, total_bytes, job_id,
        )
        return {
            "upload_id": upload.upload_id,
            "video_id": video_id,
            "job_id": job_id,
            "status": "completed",
        }

    # ------------------------------------------------------------------
    def status(self, upload_id: str) -> dict:
        validate_id(upload_id, "upload_id")
        upload = self.uploads.get(upload_id)
        if upload is None:
            raise NotFoundError(f"upload '{upload_id}' not found")
        received = self.uploads.received_chunk_indices(upload_id)
        chunks = self.uploads.list_chunks(upload_id)
        received_bytes = sum(int(c["size_bytes"]) for c in chunks)
        return self._status_dict(upload, len(received), received_bytes)

    def _status_dict(self, upload: Upload, received: int, received_bytes: int) -> dict:
        progress = (
            round(received / upload.total_chunks * 100, 2)
            if upload.total_chunks else 0.0
        )
        return {
            **upload.to_dict(),
            "received_chunks": received,
            "received_bytes": received_bytes,
            "progress": progress,
        }

    # ------------------------------------------------------------------
    def abort(self, upload_id: str) -> dict:
        validate_id(upload_id, "upload_id")
        upload = self.uploads.get(upload_id)
        if upload is None:
            raise NotFoundError(f"upload '{upload_id}' not found")
        if upload.status == "completed":
            raise ConflictError("cannot abort a completed upload")
        self.storage.delete_upload_dir(upload_id)
        self.uploads.delete(upload_id)
        self.chunk_locks.forget(upload_id)
        if self.limiter is not None:
            self.limiter.release(upload_id)
        metrics.inc("uploads.aborted")
        metrics.set_gauge("uploads.active", float(self._active_count()))
        log.info("UPLOAD ABORTED upload=%s", upload_id)
        return {"upload_id": upload_id, "status": "aborted"}

    def cleanup_abandoned(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self.settings.max_upload_age_hours)
        ).isoformat(timespec="seconds")
        stale = self.uploads.list_stale(cutoff)
        for upload in stale:
            self.storage.delete_upload_dir(upload.upload_id)
            self.uploads.delete(upload.upload_id)
            self.chunk_locks.forget(upload.upload_id)
            if self.limiter is not None:
                self.limiter.release(upload.upload_id)
            log.info("Cleaned up abandoned upload %s", upload.upload_id)
        return len(stale)
