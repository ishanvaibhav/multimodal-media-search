from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ...infrastructure.database import Database
from ...utils import now_iso


class SearchHistoryRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        query: str,
        filters: dict,
        result_count: int,
        mode: str = "accurate",
        latency_ms: int | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO search_history (query, filters, result_count, mode, latency_ms, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (query, json.dumps(filters, default=str), result_count, mode, latency_ms, now_iso()),
        )

    def list(self, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,)
        )

    def clear(self) -> int:
        return self.db.execute("DELETE FROM search_history")

    def enforce_retention(self, retention_days: int, max_rows: int) -> int:
        """Delete history older than ``retention_days`` and cap total rows."""
        deleted = 0
        if retention_days > 0:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=retention_days)
            ).isoformat(timespec="seconds")
            deleted += self.db.execute(
                "DELETE FROM search_history WHERE created_at < ?", (cutoff,)
            )
        if max_rows > 0:
            deleted += self.db.execute(
                """
                DELETE FROM search_history WHERE id IN (
                    SELECT id FROM search_history ORDER BY id DESC LIMIT -1 OFFSET ?
                )
                """,
                (max_rows,),
            )
        return deleted
