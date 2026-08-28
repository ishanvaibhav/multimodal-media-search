from __future__ import annotations

from typing import Optional

from ...domain.models import Video
from ...infrastructure.database import Database
from ...utils import date_range_condition, now_iso


class VideoRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, video: Video) -> None:
        self.db.execute(
            """
            INSERT INTO videos (video_id, filename, original_filename, path, size_bytes,
                duration_seconds, fps, width, height, codec, container, bitrate, has_audio,
                creation_time, media_type, status, frame_count, upload_id, error,
                needs_reconciliation, uploaded_at, indexed_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                video.video_id, video.filename, video.original_filename, video.path,
                video.size_bytes, video.duration_seconds, video.fps, video.width,
                video.height, video.codec, video.container, video.bitrate,
                1 if video.has_audio else 0, video.creation_time, video.media_type,
                video.status, video.frame_count, video.upload_id, video.error,
                video.needs_reconciliation, video.uploaded_at,
                video.indexed_at, video.created_at, video.updated_at,
            ),
        )

    def get(self, video_id: str) -> Optional[Video]:
        row = self.db.query_one("SELECT * FROM videos WHERE video_id = ?", (video_id,))
        return Video.from_row(row) if row else None

    def list(
        self,
        search: str = "",
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: str = "uploaded_at",
        sort_order: str = "desc",
        limit: int = 100,
        offset: int = 0,
        media_types: Optional[list[str]] = None,
        min_duration: Optional[float] = None,
        max_duration: Optional[float] = None,
        media_type: Optional[str] = None,
    ) -> list[Video]:
        where = []
        params: list = []
        if search:
            where.append("(original_filename LIKE ? OR filename LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if status:
            where.append("status = ?")
            params.append(status)
        if media_type:
            where.append("media_type = ?")
            params.append(media_type)
        if media_types:
            placeholders = ",".join("?" for _ in media_types)
            where.append(f"container IN ({placeholders})")
            params.extend(media_types)
        if min_duration is not None:
            where.append("duration_seconds >= ?")
            params.append(min_duration)
        if max_duration is not None:
            where.append("duration_seconds <= ?")
            params.append(max_duration)
        date_clause, date_params = date_range_condition(date_from, date_to, "uploaded_at")
        if date_clause:
            where.append(date_clause)
            params.extend(date_params)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        order_col = (
            sort_by
            if sort_by in {"uploaded_at", "created_at", "size_bytes", "original_filename", "filename"}
            else "uploaded_at"
        )
        order_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
        rows = self.db.query(
            f"SELECT * FROM videos {clause} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [Video.from_row(r) for r in rows]

    def list_keyset(
        self,
        after: tuple[str, str] | None,
        limit: int = 100,
        sort_by: str = "uploaded_at",
        sort_order: str = "desc",
        status: str | None = None,
    ) -> list[Video]:
        """Keyset pagination ordered by (uploaded_at, video_id) — no OFFSET.

        ``after`` is (uploaded_at, video_id) of the last item of the previous
        page; the caller passes opaque cursor values. This avoids OFFSET scans
        for large datasets (10k+ videos).
        """
        order_col = (
            sort_by if sort_by in {"uploaded_at", "created_at", "size_bytes"} else "uploaded_at"
        )
        order_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
        cmp = ">" if order_dir == "ASC" else "<"

        where = []
        params: list = []
        if status:
            where.append("status = ?")
            params.append(status)
        if after is not None:
            after_key, after_id = after
            where.append(
                f"({order_col} {cmp} ? OR ({order_col} = ? AND video_id {cmp} ?))"
            )
            params += [after_key, after_key, after_id]
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self.db.query(
            f"SELECT * FROM videos {clause} ORDER BY {order_col} {order_dir}, video_id {order_dir} "
            f"LIMIT ?",
            (*params, limit),
        )
        return [Video.from_row(r) for r in rows]

    def count(
        self,
        search: str = "",
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        media_types: Optional[list[str]] = None,
        min_duration: Optional[float] = None,
        max_duration: Optional[float] = None,
        media_type: Optional[str] = None,
    ) -> int:
        """Count with EXACTLY the same filter criteria as ``list``."""
        where = []
        params: list = []
        if search:
            where.append("(original_filename LIKE ? OR filename LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if status:
            where.append("status = ?")
            params.append(status)
        if media_type:
            where.append("media_type = ?")
            params.append(media_type)
        if media_types:
            placeholders = ",".join("?" for _ in media_types)
            where.append(f"container IN ({placeholders})")
            params.extend(media_types)
        if min_duration is not None:
            where.append("duration_seconds >= ?")
            params.append(min_duration)
        if max_duration is not None:
            where.append("duration_seconds <= ?")
            params.append(max_duration)
        date_clause, date_params = date_range_condition(date_from, date_to, "uploaded_at")
        if date_clause:
            where.append(date_clause)
            params.extend(date_params)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        row = self.db.query_one(f"SELECT COUNT(*) AS n FROM videos {clause}", params)
        return int(row["n"]) if row else 0

    def ids_uploaded_between(self, date_from: str, date_to: str) -> list[str]:
        """Video ids whose upload date falls inside [date_from, date_to].

        Date-only bounds are normalized (a date-only ``date_to`` includes the
        whole day via an exclusive next-midnight upper bound).
        """
        clause, params = date_range_condition(date_from, date_to, "uploaded_at")
        sql = "SELECT video_id FROM videos"
        if clause:
            sql += f" WHERE {clause}"
        rows = self.db.query(sql, params)
        return [r["video_id"] for r in rows]

    def ids_matching(self, media_types=None, min_duration=None, max_duration=None) -> list[str]:
        where = []
        params: list = []
        if media_types:
            placeholders = ",".join("?" for _ in media_types)
            where.append(f"container IN ({placeholders})")
            params.extend(media_types)
        if min_duration is not None:
            where.append("duration_seconds >= ?")
            params.append(min_duration)
        if max_duration is not None:
            where.append("duration_seconds <= ?")
            params.append(max_duration)
        sql = "SELECT video_id FROM videos"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [r["video_id"] for r in self.db.query(sql, params)]

    def ids_matching_status(self, status: str) -> list[str]:
        rows = self.db.query(
            "SELECT video_id FROM videos WHERE status = ?", (status,)
        )
        return [r["video_id"] for r in rows]

    def ids_matching_media_type(self, media_type: str) -> list[str]:
        rows = self.db.query(
            "SELECT video_id FROM videos WHERE media_type = ?", (media_type,)
        )
        return [r["video_id"] for r in rows]

    def update(self, video_id: str, **fields) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE videos SET {assignments} WHERE video_id = ?",
            (*fields.values(), video_id),
        )

    def delete(self, video_id: str) -> int:
        return self.db.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))

    def set_status(self, video_id: str, status: str, error: str | None = None) -> None:
        self.update(video_id, status=status, error=error)

    def touch_indexed(self, video_id: str, frame_count: int, indexed_at: str) -> None:
        self.update(video_id, frame_count=frame_count, indexed_at=indexed_at)
