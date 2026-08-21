"""Background video indexing pipeline.

Stages: VALIDATING -> PROBING -> EXTRACTING_FRAMES -> DEDUPLICATING ->
EMBEDDING -> INDEXING -> FINALIZING -> COMPLETED (or FAILED / CANCELLED).

**Concurrency safety**: all mutable pipeline state lives in a per-job
``_JobContext`` object — nothing is stored on the service instance, so
concurrent jobs can never cross-contaminate.

Memory is bounded: ffmpeg works from disk, frames are processed one batch at a
time, and vectors are upserted incrementally into ChromaDB.
"""
from __future__ import annotations

import shutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..config import Settings
from ..domain.models import (
    Frame,
    FrameSample,
    Job,
    JobStatus,
    JobStage,
    VideoStatus,
)
from ..exceptions import JobCancelled, MediaProcessingError
from ..infrastructure.ffmpeg import _CancelledError as FFmpegCancelled
from ..infrastructure.embedding import EmbeddingService
from ..infrastructure.ffmpeg import FFmpegService
from ..infrastructure.perceptual import (
    is_embedding_method,
    make_hash_function,
    normalize_method,
    similar_hash,
)
from ..infrastructure.repositories import FrameRepository, JobRepository, VideoRepository
from ..infrastructure.storage import StorageService
from ..infrastructure.vectorstore import VectorStore
from ..logging_config import get_logger
from ..utils import now_iso

log = get_logger(__name__)

# progress weight per stage (must sum to <= 100)
_STAGE_WEIGHTS = {
    JobStage.VALIDATING.value: 4,
    JobStage.PROBING.value: 6,
    JobStage.EXTRACTING_FRAMES.value: 45,
    JobStage.DEDUPLICATING.value: 10,
    JobStage.EMBEDDING.value: 25,
    JobStage.INDEXING.value: 6,
    JobStage.FINALIZING.value: 4,
}
_STAGE_ORDER = list(_STAGE_WEIGHTS.keys())


@dataclass
class _JobContext:
    """All mutable state for a single indexing run (never shared)."""
    samples: list[FrameSample] = field(default_factory=list)
    kept: list[FrameSample] = field(default_factory=list)
    seq: int = 0
    sampled_count: int = 0
    kept_count: int = 0
    embedded_count: int = 0


class IndexingService:
    def __init__(
        self,
        settings: Settings,
        videos: VideoRepository,
        frames_repo: FrameRepository,
        jobs: JobRepository,
        storage: StorageService,
        ffmpeg: FFmpegService,
        embedding: EmbeddingService,
        vectorstore: VectorStore,
    ):
        self.settings = settings
        self.videos = videos
        self.frames = frames_repo
        self.jobs = jobs
        self.storage = storage
        self.ffmpeg = ffmpeg
        self.embedding = embedding
        self.vectorstore = vectorstore
        self._dedup_method = normalize_method(settings.dedup_method)

    # ------------------------------------------------------------------
    def run_job(self, job: Job, cancel_check: Callable[[], bool]) -> None:
        video = self.videos.get(job.video_id)
        if video is None:
            self.jobs.transition(
                job.job_id, JobStatus.RUNNING.value, JobStatus.FAILED.value,
                error="video not found",
            )
            return
        # images take a dedicated path (decode + single-vector embed) — never FFmpeg
        if video.media_type == "image":
            self._run_image_job(job, video, cancel_check)
            return
        try:
            self.jobs.transition(job.job_id, JobStatus.QUEUED.value, JobStatus.RUNNING.value)
        except Exception:
            # already cancelled before we started
            return
        self.videos.update(video.video_id, status=VideoStatus.VALIDATING.value)

        ctx = _JobContext()
        base = 0.0
        try:
            for stage in _STAGE_ORDER:
                if cancel_check():
                    raise JobCancelled("job cancelled")
                self._set_stage(job.job_id, stage, base, video.video_id)
                try:
                    getattr(self, f"_stage_{stage}")(job, video, ctx, cancel_check)
                except FFmpegCancelled:
                    # ffmpeg observed cancellation during extraction
                    raise JobCancelled("job cancelled (ffmpeg terminated)") from None
                base += _STAGE_WEIGHTS[stage]
                self.jobs.set_progress(job.job_id, stage, min(base, 100.0))

            self.videos.update(video.video_id, status=VideoStatus.READY.value)
            self.jobs.transition(
                job.job_id, JobStatus.RUNNING.value, JobStatus.COMPLETED.value,
                progress=100.0, current_stage="completed", error=None,
            )
            log.info(
                "JOB COMPLETE video=%s frames=%d",
                video.video_id, self.frames.count_for_video(video.video_id),
            )
        except JobCancelled:
            log.info("Job %s cancelled", job.job_id)
            self.rollback_partial(video.video_id)
            self.videos.update(video.video_id, status=VideoStatus.CANCELLED.value)
            try:
                self.jobs.transition(
                    job.job_id, JobStatus.CANCELLING.value, JobStatus.CANCELLED.value,
                    current_stage="cancelled", error=None,
                )
            except Exception:
                self.jobs.transition(
                    job.job_id, JobStatus.RUNNING.value, JobStatus.CANCELLED.value,
                    current_stage="cancelled", error=None,
                )
        except Exception as exc:  # noqa: BLE001 - pipeline must never crash the worker
            log.error("Job %s failed: %s\n%s", job.job_id, exc, traceback.format_exc())
            # rollback the partial index; if rollback itself fails, mark the
            # video as needing reconciliation so it is surfaced (never hidden).
            rollback_error = self._rollback_with_reconciliation(video.video_id)
            error_msg = str(exc)
            if rollback_error:
                error_msg = (
                    f"{error_msg} | ROLLBACK INCOMPLETE: {rollback_error} | "
                    "RECONCILIATION_REQUIRED"
                )
            self.videos.update(
                video.video_id, status=VideoStatus.FAILED.value, error=error_msg
            )
            try:
                self.jobs.transition(
                    job.job_id, JobStatus.RUNNING.value, JobStatus.FAILED.value,
                    current_stage="failed", error=error_msg,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _run_image_job(self, job: Job, video, cancel_check) -> None:
        """Image indexing pipeline: validate → embed (single vector) → index.

        Images NEVER pass through FFmpeg. The image itself is stored as a
        single coarse frame (timestamp 0) so it flows through the unified
        semantic search with the same embedding space as video frames.
        """
        try:
            self.jobs.transition(job.job_id, JobStatus.QUEUED.value, JobStatus.RUNNING.value)
        except Exception:
            return
        self.videos.update(video.video_id, status=VideoStatus.VALIDATING.value)

        try:
            if cancel_check():
                raise JobCancelled("job cancelled")

            # VALIDATING: decode + dimension/pixel guard
            from PIL import Image

            img_path = self.storage.resolve_in(self.settings.media_dir, video.path)
            try:
                with Image.open(img_path) as im:
                    im.verify()
            except Exception as exc:
                raise MediaProcessingError(f"image is not decodable: {exc.__class__.__name__}") from exc
            with Image.open(img_path) as im:
                w, h = im.size
                if w * h > self.settings.max_image_pixels or max(w, h) > self.settings.max_image_dimension:
                    raise MediaProcessingError("image exceeds dimension limits")
            self.jobs.set_progress(job.job_id, "validating", 10, frames_total=1)
            self.videos.update(video.video_id, width=w, height=h)
            log.info("IMAGE VALIDATION ok image=%s %dx%d", video.video_id, w, h)

            if cancel_check():
                raise JobCancelled("job cancelled")

            # EMBEDDING: single image -> one normalized vector
            self.jobs.set_progress(job.job_id, "embedding", 40, frames_total=1)
            emb = self.embedding.embed_images([img_path])[0]

            # INDEXING: one frame row + one vector (atomic-ish, ordered)
            self.jobs.set_progress(job.job_id, "indexing", 70, frames_total=1)
            frame_id = f"{video.video_id}_000000"
            frame_path = video.path  # the image IS the frame (relative to DATA_DIR)
            uploaded_epoch = _to_epoch(video.uploaded_at)
            self.frames.upsert(Frame(
                frame_id=frame_id, video_id=video.video_id,
                timestamp_seconds=0.0, frame_path=frame_path,
                frame_type="coarse",
            ))
            self.vectorstore.upsert(
                [frame_id], emb[None, :],
                [{
                    "video_id": video.video_id,
                    "frame_id": frame_id,
                    "timestamp": 0.0,
                    "frame_path": frame_path,
                    "video_path": video.path,
                    "uploaded_at": uploaded_epoch or 0.0,
                    "duration": 0.0,
                    "media_type": "image",
                }],
            )

            # FINALIZING: thumbnail (scaled copy) + ready
            self.jobs.set_progress(job.job_id, "finalizing", 90, frames_total=1)
            thumb = self.storage.thumbnail_path(video.video_id)
            self._make_image_thumbnail(img_path, thumb)
            self.videos.touch_indexed(video.video_id, 1, now_iso())
            self.videos.update(video.video_id, status=VideoStatus.READY.value)
            self.jobs.transition(
                job.job_id, JobStatus.RUNNING.value, JobStatus.COMPLETED.value,
                progress=100.0, current_stage="completed", frames_processed=1,
                frames_total=1, frames_sampled=1, frames_kept=1, frames_embedded=1,
            )
            log.info("IMAGE JOB COMPLETE image=%s", video.video_id)
        except JobCancelled:
            self.rollback_partial(video.video_id)
            self.videos.update(video.video_id, status=VideoStatus.CANCELLED.value)
            try:
                self.jobs.transition(
                    job.job_id, JobStatus.RUNNING.value, JobStatus.CANCELLED.value,
                    current_stage="cancelled",
                )
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            log.error("Image job %s failed: %s\n%s", job.job_id, exc, traceback.format_exc())
            rollback_error = self._rollback_with_reconciliation(video.video_id)
            error_msg = str(exc)
            if rollback_error:
                error_msg = f"{error_msg} | ROLLBACK INCOMPLETE: {rollback_error}"
            self.videos.update(video.video_id, status=VideoStatus.FAILED.value, error=error_msg)
            try:
                self.jobs.transition(
                    job.job_id, JobStatus.RUNNING.value, JobStatus.FAILED.value,
                    current_stage="failed", error=error_msg,
                )
            except Exception:
                pass

    def _make_image_thumbnail(self, img_path: Path, out_path: Path, width: int = 480) -> None:
        from PIL import Image

        with Image.open(img_path) as im:
            im = im.convert("RGB")
            if im.width > width:
                h = int(im.height * width / im.width)
                im = im.resize((width, h), Image.LANCZOS)
            im.save(out_path, "JPEG", quality=85)

    # -- stage helpers ---------------------------------------------------
    def _set_stage(self, job_id: str, stage: str, base: float, video_id: str) -> None:
        self.jobs.set_progress(job_id, stage, base)
        self.videos.update(video_id, status=stage)
        log.info("Stage %s video=%s", stage.upper(), video_id)

    # -- individual stages ------------------------------------------------
    def _stage_validating(self, job: Job, video, ctx, cancel_check) -> None:
        path = self.storage.resolve_in(self.settings.media_dir, video.path)
        if not path.exists():
            raise MediaProcessingError(f"video file missing on disk: {path}")
        if path.stat().st_size == 0:
            raise MediaProcessingError("video file is empty")
        log.info("VIDEO VALIDATION ok video=%s", video.video_id)

    def _stage_probing(self, job: Job, video, ctx, cancel_check) -> None:
        path = self.storage.resolve_in(self.settings.media_dir, video.path)
        info = self.ffmpeg.probe(path)
        self.videos.update(
            video.video_id,
            duration_seconds=info.duration,
            fps=info.fps,
            width=info.width,
            height=info.height,
            codec=info.codec,
            container=info.container,
            bitrate=info.bitrate,
            has_audio=1 if info.has_audio else 0,
            creation_time=info.creation_time,
        )
        video.duration_seconds = info.duration
        log.info(
            "FFPROBE video=%s duration=%s codec=%s %sx%s",
            video.video_id, info.duration, info.codec, info.width, info.height,
        )

    def _stage_extracting_frames(self, job: Job, video, ctx, cancel_check) -> None:
        video_path = self.storage.resolve_in(self.settings.media_dir, video.path)
        out_dir = self.storage.video_frame_dir(video.video_id)
        interval = self.settings.frame_interval_seconds
        estimate = (
            int(video.duration_seconds / interval) + 1
            if video.duration_seconds else 0
        )
        self.jobs.set_progress(
            job.job_id, JobStage.EXTRACTING_FRAMES.value, 0,
            frames_processed=0, frames_total=estimate, frames_sampled=0,
        )

        def on_progress(done: int, _total: int) -> None:
            frac = (done / estimate) if estimate else 0.0
            prog = min(0.99, frac) * _STAGE_WEIGHTS[JobStage.EXTRACTING_FRAMES.value]
            ctx.sampled_count = done
            self.jobs.set_progress(
                job.job_id, JobStage.EXTRACTING_FRAMES.value, prog,
                frames_processed=done, frames_total=estimate, frames_sampled=done,
            )

        ctx.samples = self.ffmpeg.extract_frames(
            video_path, out_dir, interval,
            on_progress=on_progress, cancel_check=cancel_check,
            timeout=self.settings.frame_extraction_timeout_seconds,
        )
        ctx.sampled_count = len(ctx.samples)
        log.info("FRAME EXTRACTION video=%s frames=%d", video.video_id, len(ctx.samples))

    def _stage_deduplicating(self, job: Job, video, ctx, cancel_check) -> None:
        samples = ctx.samples
        total = len(samples)
        if total == 0:
            ctx.kept = []
            return

        if self._dedup_method == "none":
            ctx.kept = list(samples)
        elif is_embedding_method(self._dedup_method):
            ctx.kept = self._dedup_by_embedding(samples, ctx, job, cancel_check)
        else:
            ctx.kept = self._dedup_by_hash(samples, ctx, job, cancel_check)

        ctx.kept_count = len(ctx.kept)
        self.jobs.set_progress(
            job.job_id, JobStage.DEDUPLICATING.value,
            _STAGE_WEIGHTS[JobStage.DEDUPLICATING.value],
            frames_kept=ctx.kept_count,
        )
        log.info(
            "DEDUPLICATION video=%s method=%s kept=%d dropped=%d",
            video.video_id, self._dedup_method, len(ctx.kept), total - len(ctx.kept),
        )

    def _dedup_by_hash(self, samples, ctx, job, cancel_check) -> list[FrameSample]:
        threshold = self.settings.dedup_threshold
        hash_fn = make_hash_function(self._dedup_method)
        kept: list[FrameSample] = []
        prev_hash: Optional[int] = None
        total = len(samples)
        for i, sample in enumerate(samples):
            if cancel_check():
                raise JobCancelled("job cancelled")
            h = int(hash_fn(sample.path))
            if prev_hash is not None and similar_hash(h, prev_hash, threshold):
                sample.path.unlink(missing_ok=True)  # near-duplicate: drop file
            else:
                kept.append(sample)
                prev_hash = h
            self.jobs.set_progress(
                job.job_id, JobStage.DEDUPLICATING.value,
                (i + 1) / total * _STAGE_WEIGHTS[JobStage.DEDUPLICATING.value],
            )
        return kept

    def _dedup_by_embedding(self, samples, ctx, job, cancel_check) -> list[FrameSample]:
        """Deduplicate using embedding cosine similarity between neighbours."""
        threshold = self.settings.dedup_threshold
        kept: list[FrameSample] = []
        prev_emb = None
        batch = self.embedding.batch_size if hasattr(self.embedding, "batch_size") else 16
        total = len(samples)

        # embed in batches but compare sequentially
        embs = []
        for start in range(0, total, batch):
            if cancel_check():
                raise JobCancelled("job cancelled")
            embs.extend(self.embedding.embed_images([s.path for s in samples[start:start + batch]]))
        import numpy as np

        for i, sample in enumerate(samples):
            emb = embs[i]
            if prev_emb is not None and float(emb @ prev_emb) >= threshold:
                sample.path.unlink(missing_ok=True)
            else:
                kept.append(sample)
                prev_emb = emb
            self.jobs.set_progress(
                job.job_id, JobStage.DEDUPLICATING.value,
                (i + 1) / total * _STAGE_WEIGHTS[JobStage.DEDUPLICATING.value],
            )
        return kept

    def _stage_embedding(self, job: Job, video, ctx, cancel_check) -> None:
        kept = ctx.kept
        total = len(kept)
        if total == 0:
            raise MediaProcessingError("no frames remained after deduplication")

        uploaded_epoch = _to_epoch(video.uploaded_at)
        batch_size = self.embedding.batch_size if hasattr(self.embedding, "batch_size") else 16
        done = 0
        for start in range(0, total, batch_size):
            if cancel_check():
                raise JobCancelled("job cancelled")
            batch = kept[start : start + batch_size]
            embs = self.embedding.embed_images([s.path for s in batch])

            ids, metas, frames = [], [], []
            for sample, emb in zip(batch, embs):
                frame_id = f"{video.video_id}_{ctx.seq:06d}"
                ctx.seq += 1
                frame_path = self.storage.to_stored_path(sample.path)
                ids.append(frame_id)
                metas.append({
                    "video_id": video.video_id,
                    "frame_id": frame_id,
                    "timestamp": float(sample.timestamp_seconds),
                    "frame_path": frame_path,
                    "video_path": video.path,  # already relative
                    "uploaded_at": uploaded_epoch or 0.0,
                    "duration": video.duration_seconds or 0.0,
                })
                frames.append(Frame(
                    frame_id=frame_id, video_id=video.video_id,
                    timestamp_seconds=float(sample.timestamp_seconds),
                    frame_path=frame_path,
                ))
            self.frames.insert_many(frames)
            self.vectorstore.upsert(ids, embs, metas)
            done += len(batch)
            ctx.embedded_count = done
            self.jobs.set_progress(
                job.job_id, JobStage.EMBEDDING.value,
                done / total * _STAGE_WEIGHTS[JobStage.EMBEDDING.value],
                frames_processed=done, frames_total=total, frames_embedded=done,
            )
        log.info("EMBEDDING processed=%d/%d dim=%d", done, total, self.embedding.dim)

    def _stage_indexing(self, job: Job, video, ctx, cancel_check) -> None:
        count = self.vectorstore.count()
        log.info("CHROMADB INDEXING vectors=%d collection=%s", count, self.settings.chroma_collection)

    def _stage_finalizing(self, job: Job, video, ctx, cancel_check) -> None:
        video_path = self.storage.resolve_in(self.settings.media_dir, video.path)
        thumb_ts = 0.0
        if video.duration_seconds:
            thumb_ts = video.duration_seconds * 0.25
        thumb = self.storage.thumbnail_path(video.video_id)
        ok = self.ffmpeg.make_thumbnail(video_path, thumb, timestamp=thumb_ts)
        if not ok and ctx.kept:
            shutil.copyfile(ctx.kept[0].path, thumb)
        # video.frame_count = ACTUAL coarse indexed frames (not fine-cache
        # artifacts, which are stored with frame_type='fine_cache')
        frame_count = self.frames.count_for_video(video.video_id, "coarse")
        self.videos.touch_indexed(video.video_id, frame_count, now_iso())
        log.info(
            "FINALIZING video=%s thumbnail=%s coarse_frames=%d",
            video.video_id, ok, frame_count,
        )

    # ------------------------------------------------------------------
    def rollback_partial(self, video_id: str) -> None:
        """Remove any partial index artifacts for a failed/cancelled job."""
        self._rollback_with_reconciliation(video_id)

    def _rollback_with_reconciliation(self, video_id: str) -> str | None:
        """Roll back a partial index. Returns a description if the rollback was
        incomplete (and flags the video as needing reconciliation)."""
        errors: list[str] = []
        try:
            self.vectorstore.delete_by_video(video_id)
        except Exception as exc:  # pragma: no cover
            errors.append(f"chroma: {exc}")
        try:
            self.frames.delete_for_video(video_id)
        except Exception as exc:  # pragma: no cover
            errors.append(f"frames: {exc}")
        frame_dir = self.settings.frames_dir / video_id
        if frame_dir.exists():
            shutil.rmtree(frame_dir, ignore_errors=True)
        if errors:
            self.videos.update(video_id, needs_reconciliation=1)
            log.error(
                "Rollback incomplete for video=%s: %s (needs_reconciliation)",
                video_id, "; ".join(errors),
            )
            return "; ".join(errors)
        return None


def _to_epoch(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None
