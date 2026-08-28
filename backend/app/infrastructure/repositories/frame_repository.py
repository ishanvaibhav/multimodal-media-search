from __future__ import annotations

from typing import Optional

from ...domain.models import Frame
from ...infrastructure.database import Database
from ...utils import now_iso


class FrameRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert_many(self, frames: list[Frame]) -> None:
        if not frames:
            return
        now = now_iso()
        rows = [
            (f.frame_id, f.video_id, f.timestamp_seconds, f.frame_path, f.frame_type, now)
            for f in frames
        ]
        self.db.executemany(
            "INSERT OR REPLACE INTO frames (frame_id, video_id, timestamp_seconds, frame_path, frame_type, created_at) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )

    def upsert(self, frame: Frame) -> None:
        """Idempotently insert a frame (deterministic id) or refresh its row."""
        self.db.execute(
            """
            INSERT INTO frames (frame_id, video_id, timestamp_seconds, frame_path, frame_type, created_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(frame_id) DO UPDATE SET
                timestamp_seconds=excluded.timestamp_seconds,
                frame_path=excluded.frame_path,
                frame_type=excluded.frame_type
            """,
            (
                frame.frame_id, frame.video_id, frame.timestamp_seconds,
                frame.frame_path, frame.frame_type, now_iso(),
            ),
        )

    def get(self, frame_id: str) -> Optional[Frame]:
        row = self.db.query_one("SELECT * FROM frames WHERE frame_id = ?", (frame_id,))
        return Frame.from_row(row) if row else None

    def list_for_video(self, video_id: str) -> list[Frame]:
        rows = self.db.query(
            "SELECT * FROM frames WHERE video_id = ? ORDER BY timestamp_seconds ASC",
            (video_id,),
        )
        return [Frame.from_row(r) for r in rows]

    def between(
        self, video_id: str, lo: float, hi: float, limit: int = 50,
        frame_type: str | None = None,
    ) -> list[Frame]:
        """Frames inside [lo, hi] (context-window fetch, ordered by time).

        ``frame_type`` filters to coarse (canonical scene frames) or
        fine_cache (search artifacts) when set.
        """
        if frame_type:
            rows = self.db.query(
                """
                SELECT * FROM frames
                WHERE video_id = ? AND frame_type = ?
                  AND timestamp_seconds >= ? AND timestamp_seconds <= ?
                ORDER BY timestamp_seconds ASC LIMIT ?
                """,
                (video_id, frame_type, lo, hi, limit),
            )
        else:
            rows = self.db.query(
                """
                SELECT * FROM frames
                WHERE video_id = ? AND timestamp_seconds >= ? AND timestamp_seconds <= ?
                ORDER BY timestamp_seconds ASC LIMIT ?
                """,
                (video_id, lo, hi, limit),
            )
        return [Frame.from_row(r) for r in rows]

    def around(self, video_id: str, timestamp: float, count: int = 5) -> list[Frame]:
        rows = self.db.query(
            """
            SELECT * FROM frames WHERE video_id = ?
            ORDER BY ABS(timestamp_seconds - ?) ASC LIMIT ?
            """,
            (video_id, timestamp, count),
        )
        frames = [Frame.from_row(r) for r in rows]
        frames.sort(key=lambda f: f.timestamp_seconds)
        return frames

    def fine_between(self, video_id: str, prefix: str, lo: float, hi: float) -> list[Frame]:
        """Cached fine-search frames for a video in [lo, hi] with a given id prefix."""
        rows = self.db.query(
            """
            SELECT * FROM frames
            WHERE video_id = ? AND frame_id LIKE ? AND frame_type = 'fine_cache'
              AND timestamp_seconds >= ? AND timestamp_seconds <= ?
            ORDER BY timestamp_seconds ASC
            """,
            (video_id, f"{prefix}%", lo, hi),
        )
        return [Frame.from_row(r) for r in rows]

    def upsert_from_path(self, frame_id: str, video_id: str, path: str) -> None:
        """Rewrite just the frame_path for an existing frame (idempotent)."""
        self.db.execute(
            "UPDATE frames SET frame_path = ? WHERE frame_id = ?",
            (path, frame_id),
        )

    def count_for_video(self, video_id: str, frame_type: str | None = None) -> int:
        if frame_type:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM frames WHERE video_id = ? AND frame_type = ?",
                (video_id, frame_type),
            )
        else:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM frames WHERE video_id = ?", (video_id,)
            )
        return int(row["n"]) if row else 0

    def count_total(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM frames")
        return int(row["n"]) if row else 0

    def all_paths_for_video(self, video_id: str) -> list[tuple[str, str]]:
        rows = self.db.query(
            "SELECT frame_id, frame_path FROM frames WHERE video_id = ?", (video_id,)
        )
        return [(r["frame_id"], r["frame_path"]) for r in rows]

    def all_ids_for_video(self, video_id: str) -> list[str]:
        rows = self.db.query("SELECT frame_id FROM frames WHERE video_id = ?", (video_id,))
        return [r["frame_id"] for r in rows]

    def delete_for_video(self, video_id: str) -> int:
        return self.db.execute("DELETE FROM frames WHERE video_id = ?", (video_id,))

    def delete_all(self) -> int:
        return self.db.execute("DELETE FROM frames")
