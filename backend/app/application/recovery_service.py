"""Startup recovery and path-portability migration.

Runs once at boot:

1. Transition jobs left ``running``/``cancelling`` by a crash to a terminal
   state and roll back their partial index so the video can be re-indexed
   cleanly (or auto-requeued, if configured).
2. Rewrite any machine-specific absolute paths persisted under DATA_DIR into
   portable relative paths (database rows + Chroma metadata).
3. Clean stale temporary files and abandoned uploads.
"""
from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..domain.models import JobStatus
from ..exceptions import VectorStoreError
from ..logging_config import get_logger

log = get_logger(__name__)

_INTERRUPTED_ERROR = "interrupted by restart"


class RecoveryService:
    def __init__(self, container):
        self.container = container
        self.settings: Settings = container.settings
        self.jobs = container.job_repo
        self.videos = container.video_repo
        self.frames = container.frame_repo
        self.vectorstore = container.vectorstore
        self.indexing = container.indexing_service
        self.storage = container.storage

    # ------------------------------------------------------------------
    def recover_interrupted_jobs(self) -> dict:
        interrupted = [
            j
            for j in self.jobs.list(limit=1000)
            if j.status in (JobStatus.RUNNING.value, JobStatus.CANCELLING.value)
        ]
        recovered = 0
        requeued = 0
        for job in interrupted:
            video_id = job.video_id
            log.warning(
                "Recovering interrupted job %s (video=%s, stage=%s)",
                job.job_id, video_id, job.current_stage,
            )
            self.indexing.rollback_partial(video_id)
            self.videos.update(
                video_id, status="failed", error=_INTERRUPTED_ERROR
            )
            self.jobs.update(
                job.job_id, status=JobStatus.FAILED.value, error=_INTERRUPTED_ERROR
            )
            recovered += 1
            if self.settings.auto_requeue_on_restart and self.videos.get(video_id):
                # checkpoint the retry so a requeue is never duplicated and the
                # attempt count is observable
                self.container.job_service.create_index_job(
                    video_id, type="index"
                )
                self.jobs.update(job.job_id, retry_count=job.retry_count + 1)
                self.videos.update(video_id, status="queued", error=None)
                requeued += 1
        if recovered:
            log.info("Recovered %d interrupted jobs (requeued=%d)", recovered, requeued)
        return {"interrupted": recovered, "requeued": requeued}

    # ------------------------------------------------------------------
    def normalize_paths(self) -> dict:
        """Rewrite absolute paths under DATA_DIR to portable relative paths."""
        data_root = self.settings.data_dir_path.resolve()
        counts = {"videos": 0, "frames": 0, "chroma": 0}

        for video in self.videos.list(limit=100000):
            new = self._relativize(video.path, data_root)
            if new != video.path:
                self.videos.update(video.video_id, path=new)
                counts["videos"] += 1

        all_frames = self._all_frame_rows()
        for frame_id, video_id, path in all_frames:
            new = self._relativize(path, data_root)
            if new != path:
                self.frames.upsert_from_path(frame_id, video_id, new)
                counts["frames"] += 1

        counts["chroma"] = self._normalize_chroma_metadata(data_root)
        if any(counts.values()):
            log.info("Path normalization migrated: %s", counts)
        return counts

    def _relativize(self, stored: str, data_root: Path) -> str:
        try:
            p = Path(stored)
            if p.is_absolute():
                rel = p.resolve().relative_to(data_root)
                return rel.as_posix()
        except (ValueError, OSError):
            pass
        return stored

    def _all_frame_rows(self) -> list[tuple[str, str, str]]:
        try:
            rows = self.container.database.query(
                "SELECT frame_id, video_id, frame_path FROM frames"
            )
            return [(r["frame_id"], r["video_id"], r["frame_path"]) for r in rows]
        except Exception:
            return []

    def _normalize_chroma_metadata(self, data_root: Path) -> int:
        """Best-effort rewrite of video_path/frame_path in Chroma metadata."""
        import chromadb

        count = 0
        try:
            client = chromadb.PersistentClient(path=str(self.settings.chroma_dir))
            coll = client.get_collection(self.settings.chroma_collection)
            batch = coll.get(include=["metadatas"])
            ids = batch.get("ids") or []
            metas = batch.get("metadatas") or []
            updates = []
            for cid, meta in zip(ids, metas):
                meta = dict(meta or {})
                changed = False
                for key in ("frame_path", "video_path"):
                    if key in meta:
                        new = self._relativize(str(meta[key]), data_root)
                        if new != meta[key]:
                            meta[key] = new
                            changed = True
                if changed:
                    updates.append(cid)
                    coll.update(ids=[cid], metadatas=[meta])
                    count += 1
        except Exception as exc:
            log.warning("Chroma metadata path migration skipped: %s", exc)
        return count

    # ------------------------------------------------------------------
    def clean_temp(self) -> int:
        n = self.storage.clear_temp()
        if n:
            log.info("Cleaned %d stale temp files", n)
        return n

    def cleanup_history(self) -> int:
        """Enforce search-history retention (idempotent)."""
        deleted = self.container.history_repo.enforce_retention(
            self.settings.search_history_retention_days,
            self.settings.max_search_history_rows,
        )
        if deleted:
            log.info("History retention: deleted %d rows", deleted)
        return deleted

    def cleanup_stale_fine_cache(self) -> int:
        """Remove fine-cache interval manifests whose video no longer exists."""
        videos = {v.video_id for v in self.videos.list(limit=100000)}
        rows = self.container.database.query(
            "SELECT DISTINCT video_id FROM fine_cache_intervals"
        )
        removed = 0
        for row in rows:
            if row["video_id"] not in videos:
                self.container.fine_cache_repo.invalidate(row["video_id"])
                removed += 1
        if removed:
            log.info("Removed %d stale fine-cache manifests", removed)
        return removed
