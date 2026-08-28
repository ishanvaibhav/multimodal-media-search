"""Storage consistency checker.

Detects drift between the four consistency domains:

  * database -> filesystem (missing files)
  * filesystem -> database (orphan files)
  * database -> Chroma     (missing vectors)
  * Chroma -> database     (orphan vectors)
  * jobs -> videos         (orphan jobs)

The checker is read-only by default. ``repair=True`` performs only *safe*
fixes: deleting orphan frame files, removing orphan vectors, and failing
orphan jobs. It never deletes database records that might be recovered.
"""
from __future__ import annotations

from pathlib import Path

from ..logging_config import get_logger

log = get_logger(__name__)


class ConsistencyService:
    def __init__(self, container):
        self.container = container
        self.settings = container.settings
        self.db = container.database
        self.storage = container.storage
        self.frames = container.frame_repo
        self.videos = container.video_repo
        self.jobs = container.job_repo
        self.vectorstore = container.vectorstore

    # ------------------------------------------------------------------
    def check(self, repair: bool = False) -> dict:
        result = {
            "videos": 0,
            "frames": 0,
            "vectors": self.vectorstore.count(),
            "missing_files": [],
            "orphan_files": 0,
            "missing_vectors": [],
            "orphan_vectors": [],
            "orphan_jobs": [],
            "repaired": {},
        }

        videos = self.videos.list(limit=100000)
        result["videos"] = len(videos)

        video_ids = set()
        # -- DB -> filesystem ------------------------------------------
        for video in videos:
            video_ids.add(video.video_id)
            try:
                vpath = self.storage.resolve_in(self.settings.media_dir, video.path)
                if not vpath.exists():
                    result["missing_files"].append(
                        {"kind": "video", "video_id": video.video_id, "path": video.path}
                    )
            except Exception:
                result["missing_files"].append(
                    {"kind": "video", "video_id": video.video_id, "path": video.path}
                )

        frame_rows = self.db.query("SELECT frame_id, video_id, frame_path, frame_type FROM frames")
        result["frames"] = len(frame_rows)
        db_frame_ids = set()
        db_frame_paths = set()          # resolved absolute paths of every DB frame
        coarse_frame_ids = set()        # frames that SHOULD have a vector
        fine_count = 0                  # cached fine-search frames (no vector expected)
        for row in frame_rows:
            db_frame_ids.add(row["frame_id"])
            # explicit frame_type (never inferred from id string patterns)
            if (row.get("frame_type") or "coarse") == "fine_cache":
                fine_count += 1
            else:
                coarse_frame_ids.add(row["frame_id"])
            try:
                # image "frames" live under media/, so resolve against DATA_DIR
                fpath = self.storage.resolve_in_data(row["frame_path"])
                db_frame_paths.add(fpath)
                if not fpath.exists():
                    result["missing_files"].append(
                        {"kind": "frame", "frame_id": row["frame_id"], "path": row["frame_path"]}
                    )
            except Exception:
                result["missing_files"].append(
                    {"kind": "frame", "frame_id": row["frame_id"], "path": row["frame_path"]}
                )
        result["cached_fine_frames"] = fine_count

        # -- filesystem -> DB (orphan frame files) ----------------------
        orphan_files = []
        frames_root = self.settings.frames_dir
        if frames_root.exists():
            for f in frames_root.rglob("*.jpg"):
                if f.resolve() not in db_frame_paths:
                    orphan_files.append(str(f))
        result["orphan_files"] = len(orphan_files)
        result["orphan_file_list"] = orphan_files[:100]

        # -- DB -> Chroma (missing vectors; only coarse frames) ----------
        chroma_ids = self.vectorstore.all_ids()
        missing_vectors = [fid for fid in coarse_frame_ids if fid not in chroma_ids]
        result["missing_vectors"] = missing_vectors[:100]
        result["missing_vector_count"] = len(missing_vectors)

        # -- Chroma -> DB (orphan vectors) ------------------------------
        orphan_vectors = [cid for cid in chroma_ids if cid not in db_frame_ids]
        result["orphan_vectors"] = orphan_vectors[:100]
        result["orphan_vector_count"] = len(orphan_vectors)

        # -- jobs -> videos ---------------------------------------------
        orphan_jobs = [
            {"job_id": j.job_id, "video_id": j.video_id}
            for j in self.jobs.orphaned()
        ]
        result["orphan_jobs"] = orphan_jobs

        # -- videos flagged for reconciliation ---------------------------
        reconciling = [
            {"video_id": v.video_id, "error": v.error}
            for v in videos
            if getattr(v, "needs_reconciliation", 0)
        ]
        result["reconciliation_required"] = reconciling
        result["reconciliation_required_count"] = len(reconciling)

        # -- fine-cache manifests -> frames/files ------------------------
        self._check_fine_cache(result, video_ids)

        # -- model registry <-> Chroma metadata --------------------------
        result["model_mismatch"] = bool(getattr(self.vectorstore, "model_mismatch", False))
        result["model_consistency"] = self._check_model_consistency()

        # -- repair (safe-only) ------------------------------------------
        if repair:
            repaired = {"orphan_files": 0, "orphan_vectors": 0, "orphan_jobs": 0,
                        "orphan_manifests": 0, "invalid_manifests": 0}
            for f in orphan_files:
                try:
                    Path(f).unlink(missing_ok=True)
                    repaired["orphan_files"] += 1
                except OSError:
                    pass
            for cid in orphan_vectors:
                try:
                    self.vectorstore._collection.delete(ids=[cid])
                    repaired["orphan_vectors"] += 1
                except Exception:
                    pass
            for oj in orphan_jobs:
                self.jobs.update(oj["job_id"], status="failed", error="video deleted")
                repaired["orphan_jobs"] += 1
            # safe fine-cache repair: drop invalid/orphan manifests so the next
            # search regenerates the window (coarse data is never touched)
            for m in result["orphan_manifests"]:
                self.container.fine_cache_repo.invalidate(m["video_id"])
                repaired["orphan_manifests"] += 1
            for m in result["invalid_manifests"]:
                self.container.fine_cache_repo.invalidate_interval(
                    m["video_id"], m["interval_ms"], m["window_start"], m["window_end"]
                )
                repaired["invalid_manifests"] += 1
            result["repaired"] = repaired
        return result

    # ------------------------------------------------------------------
    def _check_fine_cache(self, result: dict, video_ids: set[str]) -> None:
        orphan_manifests: list[dict] = []
        invalid_manifests: list[dict] = []
        intervals = self.container.fine_cache_repo.all_intervals()
        for row in intervals:
            vid = row["video_id"]
            if vid not in video_ids:
                orphan_manifests.append({"video_id": vid, "window_start": row["window_start"],
                                         "window_end": row["window_end"]})
                continue
            # verify the interval's ACTUAL stored frames (never reconstruct ids
            # by arithmetic — FFmpeg's pts_time rounding is authoritative)
            prefix = f"{vid}_fine_{int(row['interval_ms']):d}_"
            rows = self.frames.fine_between(
                vid, prefix, float(row["window_start"]), float(row["window_end"])
            )
            missing_files = 0
            for f in rows:
                try:
                    fpath = self.storage.resolve_in(self.settings.frames_dir, f.frame_path)
                    if not fpath.exists():
                        missing_files += 1
                except Exception:
                    missing_files += 1
            if not rows or missing_files:
                invalid_manifests.append({
                    "video_id": vid, "interval_ms": row["interval_ms"],
                    "window_start": row["window_start"], "window_end": row["window_end"],
                    "missing_frames": missing_files, "frame_rows": len(rows),
                })
        result["orphan_manifests"] = orphan_manifests
        result["invalid_manifests"] = invalid_manifests
        result["orphan_manifest_count"] = len(orphan_manifests)
        result["invalid_manifest_count"] = len(invalid_manifests)
        result["fine_cache_intervals"] = len(intervals)

    def _check_model_consistency(self) -> dict:
        """Compare the active model registry row with sampled Chroma metadata."""
        registry = self.db.query_one(
            "SELECT * FROM model_info WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        )
        chroma_models: set[str] = set()
        try:
            res = self.vectorstore._collection.get(limit=50, include=["metadatas"])
            for meta in (res.get("metadatas") or []):
                if meta and meta.get("embedding_model"):
                    chroma_models.add(str(meta["embedding_model"]))
        except Exception:
            pass
        reg_model = (registry or {}).get("embedding_model")
        consistent = (not registry) or (not chroma_models) or (
            len(chroma_models) == 1 and reg_model in chroma_models
        )
        return {
            "registry_model": reg_model,
            "chroma_models": sorted(chroma_models),
            "consistent": consistent,
        }

        # -- repair (safe-only) ------------------------------------------
        if repair:
            repaired = {"orphan_files": 0, "orphan_vectors": 0, "orphan_jobs": 0}
            for f in orphan_files:
                try:
                    Path(f).unlink(missing_ok=True)
                    repaired["orphan_files"] += 1
                except OSError:
                    pass
            for cid in orphan_vectors:
                try:
                    self.vectorstore._collection.delete(ids=[cid])
                    repaired["orphan_vectors"] += 1
                except Exception:
                    pass
            for oj in orphan_jobs:
                self.jobs.update(oj["job_id"], status="failed", error="video deleted")
                repaired["orphan_jobs"] += 1
            result["repaired"] = repaired
        return result
