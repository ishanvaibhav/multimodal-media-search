from __future__ import annotations

from typing import Optional

from ...domain.models import Upload
from ...infrastructure.database import Database
from ...utils import now_iso


class UploadRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, upload: Upload) -> None:
        self.db.execute(
            """
            INSERT INTO uploads (upload_id, filename, file_size, content_type, chunk_size,
                total_chunks, received_chunks, received_bytes, status, error,
                result_video_id, result_job_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                upload.upload_id, upload.filename, upload.file_size, upload.content_type,
                upload.chunk_size, upload.total_chunks, upload.received_chunks,
                upload.received_bytes, upload.status, upload.error,
                upload.result_video_id, upload.result_job_id,
                upload.created_at, upload.updated_at,
            ),
        )

    def transition(self, upload_id: str, from_status: str, to_status: str, **extra) -> bool:
        """Atomic CAS status transition. Returns True if this call won the race."""
        fields = dict(extra)
        fields["status"] = to_status
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        rowcount = self.db.execute(
            f"UPDATE uploads SET {assignments} WHERE upload_id = ? AND status = ?",
            (*fields.values(), upload_id, from_status),
        )
        return rowcount == 1

    def get(self, upload_id: str) -> Optional[Upload]:
        row = self.db.query_one("SELECT * FROM uploads WHERE upload_id = ?", (upload_id,))
        return Upload.from_row(row) if row else None

    def update(self, upload_id: str, **fields) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE uploads SET {assignments} WHERE upload_id = ?",
            (*fields.values(), upload_id),
        )

    def mark_chunk(self, upload_id: str, index: int, size_bytes: int, sha256: str) -> None:
        self.db.execute(
            """
            INSERT INTO upload_chunks (upload_id, chunk_index, size_bytes, sha256, received_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(upload_id, chunk_index)
            DO UPDATE SET size_bytes=excluded.size_bytes, sha256=excluded.sha256,
                          received_at=excluded.received_at
            """,
            (upload_id, index, size_bytes, sha256, now_iso()),
        )

    def list_chunks(self, upload_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index ASC",
            (upload_id,),
        )

    def get_chunk(self, upload_id: str, index: int) -> dict | None:
        return self.db.query_one(
            "SELECT * FROM upload_chunks WHERE upload_id = ? AND chunk_index = ?",
            (upload_id, index),
        )

    def received_chunk_indices(self, upload_id: str) -> set[int]:
        rows = self.db.query(
            "SELECT chunk_index FROM upload_chunks WHERE upload_id = ?", (upload_id,)
        )
        return {int(r["chunk_index"]) for r in rows}

    def delete(self, upload_id: str) -> int:
        self.db.execute("DELETE FROM upload_chunks WHERE upload_id = ?", (upload_id,))
        return self.db.execute("DELETE FROM uploads WHERE upload_id = ?", (upload_id,))

    def count_active(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM uploads WHERE status IN ('uploading','completing')"
        )
        return int(row["n"]) if row else 0

    def list_stale(self, older_than_iso: str) -> list[Upload]:
        rows = self.db.query(
            "SELECT * FROM uploads WHERE status = 'uploading' AND updated_at < ?",
            (older_than_iso,),
        )
        return [Upload.from_row(r) for r in rows]
