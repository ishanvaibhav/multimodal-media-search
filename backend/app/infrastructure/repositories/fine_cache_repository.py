from __future__ import annotations

from ...infrastructure.database import Database
from ...utils import now_iso

# Adjacency tolerance (seconds): two cached intervals whose boundary differs by
# less than this are considered contiguous (timestamps are quantised by the
# sampling interval, so exact boundary equality is expected).
_ADJACENCY_EPS = 1e-4


class FineCacheRepository:
    """Interval-based manifest for complete, configuration-matched fine caches.

    Each COMPLETE extraction window is stored as its own interval row
    ``(video_id, interval_ms, window_start, window_end)``. Disjoint windows are
    NEVER merged — coverage is computed from the actual interval set, so a
    request over [0, 15] with cached [0,5] + [10,15] correctly reports a
    missing gap [5,10].

    A row is only written after every frame of that window has been persisted
    and validated (atomic manifest commit): a crash mid-extraction leaves no
    complete row, so the next request re-extracts the gap instead of trusting a
    partial cache.
    """

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    def add_interval(
        self,
        video_id: str,
        interval_ms: int,
        window_start: float,
        window_end: float,
        frame_count: int,
        expected_count: int,
        extraction_version: str,
    ) -> None:
        """Record a COMPLETE extraction interval (atomic, idempotent)."""
        self.db.execute(
            """
            INSERT INTO fine_cache_intervals
                (video_id, interval_ms, window_start, window_end,
                 frame_count, expected_count, extraction_version, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id, interval_ms, window_start, window_end)
            DO UPDATE SET frame_count=excluded.frame_count,
                          expected_count=excluded.expected_count,
                          extraction_version=excluded.extraction_version,
                          updated_at=excluded.updated_at
            """,
            (
                video_id, interval_ms, window_start, window_end,
                frame_count, expected_count, extraction_version, now_iso(),
            ),
        )

    def intervals_for(
        self, video_id: str, interval_ms: int, extraction_version: str
    ) -> list[tuple[float, float]]:
        rows = self.db.query(
            """
            SELECT window_start, window_end FROM fine_cache_intervals
            WHERE video_id = ? AND interval_ms = ? AND extraction_version = ?
            ORDER BY window_start ASC
            """,
            (video_id, interval_ms, extraction_version),
        )
        return [(float(r["window_start"]), float(r["window_end"])) for r in rows]

    def coverage(
        self,
        video_id: str,
        interval_ms: int,
        extraction_version: str,
        lo: float,
        hi: float,
    ) -> tuple[bool, list[tuple[float, float]]]:
        """Return (fully_covered, missing_gaps) for [lo, hi].

        Missing gaps are returned as a list of (gap_start, gap_end) intervals
        that the caller should extract. An empty gap list => full coverage.
        """
        intervals = self.intervals_for(video_id, interval_ms, extraction_version)
        gaps = compute_gaps(intervals, lo, hi)
        return (len(gaps) == 0, gaps)

    def all_intervals(self) -> list[dict]:
        return self.db.query(
            "SELECT * FROM fine_cache_intervals ORDER BY video_id, window_start"
        )

    def invalidate(self, video_id: str) -> int:
        return self.db.execute(
            "DELETE FROM fine_cache_intervals WHERE video_id = ?", (video_id,)
        )

    def invalidate_interval(
        self, video_id: str, interval_ms: int, window_start: float, window_end: float
    ) -> int:
        return self.db.execute(
            """
            DELETE FROM fine_cache_intervals
            WHERE video_id = ? AND interval_ms = ? AND window_start = ? AND window_end = ?
            """,
            (video_id, interval_ms, window_start, window_end),
        )

    def delete_all(self) -> int:
        return self.db.execute("DELETE FROM fine_cache_intervals")


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping/adjacent intervals (tolerance _ADJACENCY_EPS)."""
    ordered = sorted(intervals)
    merged: list[tuple[float, float]] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1] + _ADJACENCY_EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def compute_gaps(
    intervals: list[tuple[float, float]], lo: float, hi: float
) -> list[tuple[float, float]]:
    """Compute the uncovered sub-intervals of [lo, hi] given cached intervals.

    Examples:
      cached [0,5],[5,10],[10,15], requested [0,15] -> []
      cached [0,5],[10,15],       requested [0,15] -> [(5,10)]
      cached [0,8],[8,16],        requested [4,20] -> [(16,20)]
    """
    if lo >= hi:
        return []
    clipped = []
    for s, e in intervals:
        s = max(s, lo)
        e = min(e, hi)
        if e - s > _ADJACENCY_EPS:
            clipped.append((s, e))
    merged = merge_intervals(clipped)
    gaps: list[tuple[float, float]] = []
    cursor = lo
    for s, e in merged:
        if s > cursor + _ADJACENCY_EPS:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < hi - _ADJACENCY_EPS:
        gaps.append((cursor, hi))
    return gaps
