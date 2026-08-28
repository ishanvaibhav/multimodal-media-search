"""Disk storage management.

All filesystem access goes through this service so path handling stays
centralised, traversal-safe and consistent.

**Portability**: paths persisted in the database and in Chroma metadata are
stored *relative* to ``DATA_DIR`` (e.g. ``media/<video-id>.mp4``) and resolved
through this service at runtime. Legacy absolute paths (from older databases)
are still resolved, and a startup migration rewrites them to relative form.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..exceptions import StorageError
from ..utils import ensure_within, human_size


class StorageService:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()

    # ------------------------------------------------------------------
    # Path portability: relative <-> absolute
    # ------------------------------------------------------------------
    def to_stored_path(self, absolute_path: Path) -> str:
        """Convert an absolute path to a portable relative form when it lives
        under DATA_DIR; otherwise keep the absolute form (legacy/external)."""
        try:
            rel = absolute_path.resolve().relative_to(self.settings.data_dir_path.resolve())
            return rel.as_posix()
        except ValueError:
            return str(absolute_path)

    def resolve_stored(self, stored: str, root: Optional[Path] = None) -> Path:
        """Resolve a stored (relative or legacy absolute) path back to disk."""
        if not stored:
            raise StorageError("empty stored path")
        p = Path(stored)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            base = (root or self.settings.data_dir_path).resolve()
            resolved = (base / p).resolve()
            if not resolved.is_relative_to(base):
                raise StorageError(f"stored path escapes its root: {stored!r}")
        return resolved

    def resolve_in_data(self, stored: str) -> Path:
        """Resolve a stored path and require containment inside DATA_DIR.

        Used when serving frames: an image's "frame" is the image file itself,
        stored under ``media/``, so frame paths must resolve against DATA_DIR
        (not the frames/ subdirectory) while still being traversal-safe.
        """
        resolved = self.resolve_stored(stored)
        root = self.settings.data_dir_path.resolve()
        if not resolved.is_relative_to(root):
            raise StorageError(f"stored path escapes data dir: {stored!r}")
        return resolved

    def resolve_in(self, root: Path, stored: str) -> Path:
        """Resolve a stored path and require it to be inside ``root``.

        Stored paths are relative to DATA_DIR (e.g. ``media/<id>.mp4``), so we
        resolve against DATA_DIR first and then verify containment inside the
        requested root. Legacy absolute paths resolve as-is. A tampered DB
        value can never read arbitrary files outside ``root``.
        """
        resolved = self.resolve_stored(stored)
        root_resolved = root.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise StorageError(f"stored path escapes {root}: {stored!r}")
        return resolved

    # ------------------------------------------------------------------
    # upload scratch space
    # ------------------------------------------------------------------
    def upload_dir(self, upload_id: str) -> Path:
        d = ensure_within(self.settings.uploads_dir, upload_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def chunk_path(self, upload_id: str, index: int) -> Path:
        return self.upload_dir(upload_id) / f"chunk_{index:06d}"

    def delete_upload_dir(self, upload_id: str) -> None:
        d = self.settings.uploads_dir / upload_id
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def assemble_upload(self, upload_id: str, dest_name: str, total_chunks: int) -> Path:
        """Concatenate received chunks in order into media/<dest_name>."""
        src_dir = self.settings.uploads_dir / upload_id
        dest = ensure_within(self.settings.media_dir, dest_name)
        try:
            with open(dest, "wb") as out:
                for i in range(total_chunks):
                    part = src_dir / f"chunk_{i:06d}"
                    if not part.exists():
                        raise StorageError(f"missing chunk {i} for upload {upload_id}")
                    with open(part, "rb") as src:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
        except OSError as exc:
            raise StorageError(f"failed to assemble upload: {exc}") from exc
        return dest

    # ------------------------------------------------------------------
    # media / frames / thumbnails
    # ------------------------------------------------------------------
    def video_frame_dir(self, video_id: str) -> Path:
        d = ensure_within(self.settings.frames_dir, video_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def thumbnail_path(self, video_id: str) -> Path:
        return ensure_within(self.settings.thumbnails_dir, f"{video_id}.jpg")

    def temp_path(self, name: str) -> Path:
        return ensure_within(self.settings.temp_dir, name)

    def delete_video_artifacts(self, video) -> dict:
        """Delete video file, frames and thumbnail for a video record."""
        counts = {"video_file": 0, "frames": 0, "thumbnail": 0}
        try:
            video_path = self.resolve_in(self.settings.media_dir, video.path)
            if video_path.exists():
                video_path.unlink()
                counts["video_file"] = 1
        except StorageError:
            counts["video_file"] = 0
        frame_dir = self.settings.frames_dir / video.video_id
        if frame_dir.exists():
            counts["frames"] = sum(1 for _ in frame_dir.rglob("*") if _.is_file())
            shutil.rmtree(frame_dir, ignore_errors=True)
        thumb = self.thumbnail_path(video.video_id)
        if thumb.exists():
            thumb.unlink()
            counts["thumbnail"] = 1
        return counts

    def delete_all_media_artifacts(self) -> dict:
        """Wipe all media/frame/thumbnail/upload files (used by Clear All Data)."""
        counts = {}
        for label, d in (
            ("media", self.settings.media_dir),
            ("frames", self.settings.frames_dir),
            ("thumbnails", self.settings.thumbnails_dir),
            ("uploads", self.settings.uploads_dir),
        ):
            n = 0
            if d.exists():
                n = sum(1 for _ in d.rglob("*") if _.is_file())
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            counts[label] = n
        return counts

    def clear_temp(self) -> int:
        n = 0
        if self.settings.temp_dir.exists():
            n = sum(1 for _ in self.settings.temp_dir.rglob("*") if _.is_file())
            shutil.rmtree(self.settings.temp_dir, ignore_errors=True)
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
        return n

    # ------------------------------------------------------------------
    # statistics
    # ------------------------------------------------------------------
    def storage_stats(self) -> dict:
        stats: dict = {"total_bytes": 0}
        for label, d in (
            ("media", self.settings.media_dir),
            ("frames", self.settings.frames_dir),
            ("thumbnails", self.settings.thumbnails_dir),
            ("uploads", self.settings.uploads_dir),
            ("temp", self.settings.temp_dir),
            ("chroma", self.settings.chroma_dir),
        ):
            size = self._dir_size(d)
            stats[f"{label}_bytes"] = size
            stats[f"{label}_human"] = human_size(size)
            stats["total_bytes"] += size
        stats["total_human"] = human_size(stats["total_bytes"])
        return stats

    @staticmethod
    def _dir_size(d: Path) -> int:
        total = 0
        if not d.exists():
            return 0
        for root, _dirs, files in os.walk(d):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
        return total

    @staticmethod
    def sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def has_free_space(self, needed_bytes: int) -> bool:
        try:
            usage = shutil.disk_usage(self.settings.data_dir_path)
            return usage.free >= needed_bytes
        except OSError:
            return True
