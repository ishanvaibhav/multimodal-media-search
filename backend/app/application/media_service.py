"""Media library use-cases: listing, detail, deletion, reindexing.

Delete/reindex coordinate with the background worker through the per-video
lock + cancellation protocol so a video can never be resurrected by an old
worker continuing after deletion.
"""
from __future__ import annotations

import shutil
from typing import Optional

from ..domain.models import Video
from ..exceptions import NotFoundError
from ..infrastructure import metrics
from ..infrastructure.coordinator import VideoCoordinator
from ..infrastructure.repositories import (
    FrameRepository,
    JobRepository,
    UploadRepository,
    VideoRepository,
)
from ..infrastructure.storage import StorageService
from ..infrastructure.vectorstore import VectorStore
from ..logging_config import get_logger
from ..utils import format_hms, validate_id

log = get_logger(__name__)


class MediaService:
    def __init__(
        self,
        videos: VideoRepository,
        frames: FrameRepository,
        uploads: UploadRepository,
        jobs: JobRepository,
        storage: StorageService,
        vectorstore: VectorStore,
        job_service,
        coordinator: VideoCoordinator,
        worker,
        settings,
        fine_cache=None,
    ):
        self.videos = videos
        self.frames = frames
        self.uploads = uploads
        self.jobs = jobs
        self.storage = storage
        self.vectorstore = vectorstore
        self.job_service = job_service
        self.coordinator = coordinator
        self.worker = worker
        self.settings = settings
        self.fine_cache = fine_cache

    # ------------------------------------------------------------------
    def list(
        self,
        search: str = "",
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: str = "uploaded_at",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 100,
        media_types: Optional[list[str]] = None,
        min_duration: Optional[float] = None,
        max_duration: Optional[float] = None,
        media_type: Optional[str] = None,
    ) -> dict:
        page = max(1, page)
        page_size = min(500, max(1, page_size))
        offset = (page - 1) * page_size
        items = self.videos.list(
            search=search, status=status, date_from=date_from, date_to=date_to,
            sort_by=sort_by, sort_order=sort_order, limit=page_size, offset=offset,
            media_types=media_types, min_duration=min_duration, max_duration=max_duration,
            media_type=media_type,
        )
        # total uses EXACTLY the same filtering criteria as the rows
        total = self.videos.count(
            search=search, status=status, date_from=date_from, date_to=date_to,
            media_types=media_types, min_duration=min_duration, max_duration=max_duration,
            media_type=media_type,
        )
        return {
            "items": [self._serialize(v) for v in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get(self, video_id: str) -> dict:
        validate_id(video_id, "video_id")
        video = self._require(video_id)
        data = self._serialize(video)
        data["frames"] = [
            {
                "frame_id": f.frame_id,
                "timestamp": f.timestamp_seconds,
                "timestamp_hms": format_hms(f.timestamp_seconds),
                "frame_url": f"/api/media/{video_id}/frames/{f.frame_id}",
            }
            for f in self.frames.list_for_video(video_id)
        ]
        job = self.jobs.latest_for_video(video_id)
        if job:
            data["job"] = job.to_dict()
        return data

    def delete(self, video_id: str) -> dict:
        validate_id(video_id, "video_id")
        video = self._require(video_id)

        # 1) quiesce: cancel + wait for any active job on this video
        try:
            self.coordinator.wait_until_no_active_jobs(
                video_id,
                active_lookup=self.jobs.active_for_video,
                cancel=self._cancel_job,
                timeout=self.settings.job_cancel_timeout_seconds,
            )
        except TimeoutError as exc:
            raise NotFoundError(f"could not stop active processing for '{video_id}': {exc}") from exc

        # 2) hold the per-video lock while performing destructive cleanup
        with self.coordinator.hold(video_id):
            # the video may have been deleted while we waited
            if self.videos.get(video_id) is None:
                raise NotFoundError(f"video '{video_id}' not found")
            vectors = self.vectorstore.delete_by_video(video_id)
            frame_count = self.frames.count_for_video(video_id)
            self.frames.delete_for_video(video_id)
            artifacts = self.storage.delete_video_artifacts(video)
            if self.fine_cache is not None:
                self.fine_cache.invalidate(video_id)
            self.videos.delete(video_id)
            metrics.inc("media.deleted")
        log.info(
            "MEDIA DELETED video=%s vectors=%d frames=%d files=%s",
            video_id, vectors, frame_count, artifacts,
        )
        return {
            "video_id": video_id,
            "deleted": {
                "vectors": vectors,
                "frames": frame_count,
                "video_file": artifacts.get("video_file", 0),
                "thumbnail": artifacts.get("thumbnail", 0),
                "frame_files": artifacts.get("frames", 0),
            },
        }

    def reindex(self, video_id: str) -> dict:
        validate_id(video_id, "video_id")
        video = self._require(video_id)

        # cancel + wait for any active job, then clear the existing index
        try:
            self.coordinator.wait_until_no_active_jobs(
                video_id,
                active_lookup=self.jobs.active_for_video,
                cancel=self._cancel_job,
                timeout=self.settings.job_cancel_timeout_seconds,
            )
        except TimeoutError as exc:
            raise NotFoundError(f"could not stop active processing for '{video_id}': {exc}") from exc

        with self.coordinator.hold(video_id):
            if self.videos.get(video_id) is None:
                raise NotFoundError(f"video '{video_id}' not found")
            self.vectorstore.delete_by_video(video_id)
            self.frames.delete_for_video(video_id)
            frame_dir = self.storage.settings.frames_dir / video_id
            if frame_dir.exists():
                shutil.rmtree(frame_dir, ignore_errors=True)
            if self.fine_cache is not None:
                self.fine_cache.invalidate(video_id)
            self.videos.update(
                video_id, status="queued", frame_count=0, indexed_at=None, error=None,
                needs_reconciliation=0,
            )
            job_id = self.job_service.create_index_job(video_id, type="reindex")
        log.info("REINDEX video=%s job=%s", video_id, job_id)
        return {"video_id": video_id, "job_id": job_id}

    def _cancel_job(self, job_id: str) -> None:
        try:
            self.job_service.cancel(job_id)
            self.worker.request_cancel(job_id)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("cancel job %s: %s", job_id, exc)

    # ------------------------------------------------------------------
    def _require(self, video_id: str) -> Video:
        video = self.videos.get(video_id)
        if video is None:
            raise NotFoundError(f"video '{video_id}' not found")
        return video

    def _serialize(self, v: Video) -> dict:
        is_image = v.media_type == "image"
        return {
            "video_id": v.video_id,
            "filename": v.original_filename,
            "stored_filename": v.filename,
            "media_type": v.media_type,
            "size_bytes": v.size_bytes,
            "duration_seconds": v.duration_seconds,
            "duration_hms": (None if is_image else (format_hms(v.duration_seconds) if v.duration_seconds else None)),
            "fps": v.fps,
            "width": v.width,
            "height": v.height,
            "codec": v.codec,
            "container": v.container,
            "has_audio": v.has_audio,
            "status": v.status,
            "frame_count": v.frame_count,
            "error": v.error,
            "uploaded_at": v.uploaded_at,
            "indexed_at": v.indexed_at,
            "thumbnail_url": f"/api/media/{v.video_id}/thumbnail",
            "stream_url": f"/api/media/{v.video_id}/stream",
        }
