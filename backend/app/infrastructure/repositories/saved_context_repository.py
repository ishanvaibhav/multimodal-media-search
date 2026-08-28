from __future__ import annotations

from typing import Optional

from ...domain.models import SavedContext
from ...infrastructure.database import Database
from ...utils import now_iso


class SavedContextRepository:
    """Persistence for user-saved search contexts (smallest layer that fits
    the existing single-SQLite-file architecture)."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, ctx: SavedContext) -> SavedContext:
        cur = self.db._execute(
            """
            INSERT INTO saved_contexts
                (query, video_id, filename, media_type, timestamp_seconds,
                 context_start, context_end, score, frame_id, context_text,
                 context_frames_json, reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ctx.query, ctx.video_id, ctx.filename, ctx.media_type,
                ctx.timestamp_seconds, ctx.context_start, ctx.context_end,
                ctx.score, ctx.frame_id, ctx.context_text,
                ctx.context_frames_json, ctx.reason, now_iso(),
            ),
        )
        ctx.id = cur.lastrowid
        return ctx

    def list(self, limit: int = 100) -> list[SavedContext]:
        rows = self.db.query(
            "SELECT * FROM saved_contexts ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [SavedContext.from_row(r) for r in rows]

    def get(self, ctx_id: int) -> Optional[SavedContext]:
        row = self.db.query_one(
            "SELECT * FROM saved_contexts WHERE id = ?", (ctx_id,)
        )
        return SavedContext.from_row(row) if row else None

    def delete(self, ctx_id: int) -> int:
        return self.db.execute("DELETE FROM saved_contexts WHERE id = ?", (ctx_id,))

    def delete_all(self) -> int:
        return self.db.execute("DELETE FROM saved_contexts")
