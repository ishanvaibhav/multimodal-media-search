from __future__ import annotations

from ...infrastructure.database import Database
from ...utils import now_iso


class FeedbackRepository:
    """Relevance feedback is stored for analytics/evaluation only — it does
    not (yet) influence ranking."""

    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        query: str,
        relevant: bool,
        video_id: str | None = None,
        frame_id: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO feedback (query, video_id, frame_id, timestamp, relevant, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (query, video_id, frame_id, timestamp, 1 if relevant else 0, now_iso()),
        )

    def summary(self, limit: int = 100) -> dict:
        rows = self.db.query(
            "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
        )
        pos = sum(1 for r in rows if r["relevant"])
        return {"total": len(rows), "relevant": pos, "not_relevant": len(rows) - pos, "items": rows}

    def clear(self) -> int:
        return self.db.execute("DELETE FROM feedback")
