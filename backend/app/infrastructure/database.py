"""SQLite database wrapper with a lightweight, backward-compatible migration system.

* A single connection guarded by a re-entrant lock (single-process deployment).
* WAL mode for concurrent read/write behaviour.
* Schema changes are applied via ``PRAGMA user_version`` migrations so existing
  databases are upgraded in place rather than destroyed.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from ..exceptions import DatabaseError

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    filename          TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    path              TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL,
    fps               REAL,
    width             INTEGER,
    height            INTEGER,
    codec             TEXT,
    container         TEXT,
    bitrate           INTEGER,
    has_audio         INTEGER NOT NULL DEFAULT 0,
    creation_time     TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    frame_count       INTEGER NOT NULL DEFAULT 0,
    upload_id         TEXT,
    error             TEXT,
    uploaded_at       TEXT,
    indexed_at        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_uploaded_at ON videos(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS frames (
    frame_id          TEXT PRIMARY KEY,
    video_id          TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    timestamp_seconds REAL NOT NULL,
    frame_path        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frames_video ON frames(video_id);

CREATE TABLE IF NOT EXISTS uploads (
    upload_id        TEXT PRIMARY KEY,
    filename         TEXT NOT NULL,
    file_size        INTEGER NOT NULL,
    content_type     TEXT,
    chunk_size       INTEGER NOT NULL,
    total_chunks     INTEGER NOT NULL,
    received_chunks  INTEGER NOT NULL DEFAULT 0,
    received_bytes   INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'uploading',
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS upload_chunks (
    upload_id   TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT,
    received_at TEXT,
    PRIMARY KEY (upload_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    video_id         TEXT NOT NULL,
    type             TEXT NOT NULL DEFAULT 'index',
    status           TEXT NOT NULL DEFAULT 'queued',
    progress         REAL NOT NULL DEFAULT 0,
    current_stage    TEXT NOT NULL DEFAULT 'queued',
    frames_processed INTEGER NOT NULL DEFAULT 0,
    frames_total     INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id);

CREATE TABLE IF NOT EXISTS search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    filters      TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    video_id     TEXT,
    frame_id     TEXT,
    timestamp    REAL,
    relevant     INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_info (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    embedding_model    TEXT NOT NULL,
    model_version      TEXT,
    embedding_dim      INTEGER NOT NULL,
    preprocessing_ver  TEXT NOT NULL,
    indexing_version   TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
"""

# Migration steps: (version, sql)
MIGRATIONS: list[tuple[int, str]] = [
    (
        2,
        """
        ALTER TABLE jobs ADD COLUMN frames_sampled INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN frames_kept INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN frames_embedded INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE search_history ADD COLUMN mode TEXT;
        ALTER TABLE search_history ADD COLUMN latency_ms INTEGER;
        """,
    ),
    (
        3,
        """
        ALTER TABLE uploads ADD COLUMN result_video_id TEXT;
        ALTER TABLE uploads ADD COLUMN result_job_id TEXT;
        """,
    ),
    (
        4,
        """
        ALTER TABLE frames ADD COLUMN frame_type TEXT NOT NULL DEFAULT 'coarse';
        ALTER TABLE videos ADD COLUMN needs_reconciliation INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE model_info ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_frames_video_type ON frames(video_id, frame_type);
        CREATE TABLE IF NOT EXISTS fine_cache (
            video_id           TEXT PRIMARY KEY,
            interval_ms        INTEGER NOT NULL,
            window_start       REAL NOT NULL,
            window_end         REAL NOT NULL,
            frame_count        INTEGER NOT NULL,
            expected_count     INTEGER NOT NULL,
            complete           INTEGER NOT NULL DEFAULT 0,
            extraction_version TEXT NOT NULL,
            updated_at         TEXT NOT NULL
        );
        UPDATE frames SET frame_type = 'fine_cache'
          WHERE instr(frame_id, '_fine_') > 0;
        """,
    ),
    (
        # Interval-based fine-cache model: each COMPLETE extraction interval is
        # one row. The old single-row-per-video manifest merged disjoint
        # windows into a false continuous range and is replaced (derived data).
        5,
        """
        DROP TABLE IF EXISTS fine_cache;
        CREATE TABLE IF NOT EXISTS fine_cache_intervals (
            video_id           TEXT NOT NULL,
            interval_ms        INTEGER NOT NULL,
            window_start       REAL NOT NULL,
            window_end         REAL NOT NULL,
            frame_count        INTEGER NOT NULL,
            expected_count     INTEGER NOT NULL,
            extraction_version TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            PRIMARY KEY (video_id, interval_ms, window_start, window_end)
        );
        CREATE INDEX IF NOT EXISTS idx_fine_cache_video ON fine_cache_intervals(video_id, interval_ms);
        """,
    ),
    (
        # Scale-out: job retry/checkpoint fields + query-shaping indexes for
        # 10,000+ video datasets (all additive).
        6,
        """
        ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN checkpoint TEXT;
        CREATE INDEX IF NOT EXISTS idx_frames_video_ts ON frames(video_id, timestamp_seconds);
        CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
        CREATE INDEX IF NOT EXISTS idx_search_history_created ON search_history(created_at);
        CREATE INDEX IF NOT EXISTS idx_uploads_status_updated ON uploads(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_fine_cache_video_win ON fine_cache_intervals(video_id, window_start);
        """,
    ),
    (
        # Image + video as first-class media types (additive; existing rows
        # default to 'video', which is correct for prior data).
        7,
        """
        ALTER TABLE videos ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video';
        ALTER TABLE jobs ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video';
        CREATE INDEX IF NOT EXISTS idx_videos_media_type ON videos(media_type);
        """,
    ),
    (
        # Saved contexts (copy/save/export feature). Derived data; additive.
        8,
        """
        CREATE TABLE IF NOT EXISTS saved_contexts (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            query              TEXT NOT NULL,
            video_id           TEXT NOT NULL,
            filename           TEXT NOT NULL,
            media_type         TEXT NOT NULL DEFAULT 'video',
            timestamp_seconds  REAL NOT NULL,
            context_start      REAL,
            context_end        REAL,
            score              REAL NOT NULL DEFAULT 0,
            frame_id           TEXT,
            context_text       TEXT,
            created_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_saved_contexts_created ON saved_contexts(created_at);
        """,
    ),
    (
        # Context evidence: store the representative frame list + reason so
        # export keeps complete metadata. Additive.
        9,
        """
        ALTER TABLE saved_contexts ADD COLUMN context_frames_json TEXT;
        ALTER TABLE saved_contexts ADD COLUMN reason TEXT;
        """,
    ),
]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            self._migrate()

    # -- migrations ------------------------------------------------------
    def _migrate(self) -> None:
        version = self._user_version()
        target = max((v for v, _ in MIGRATIONS), default=version)
        for step_version, sql in MIGRATIONS:
            if step_version <= version:
                continue
            try:
                self._conn.executescript(sql)
                self._conn.commit()
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                raise DatabaseError(f"migration to v{step_version} failed: {exc}") from exc
            self._set_user_version(step_version)
        if version < target:
            self._set_user_version(target)

    def _user_version(self) -> int:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def _set_user_version(self, version: int) -> None:
        self._conn.execute(f"PRAGMA user_version = {int(version)}")
        self._conn.commit()

    def schema_version(self) -> int:
        with self._lock:
            return self._user_version()

    def column_exists(self, table: str, column: str) -> bool:
        with self._lock:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(r["name"] == column for r in rows)

    # -- core API --------------------------------------------------------
    def _execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                cur = self._conn.execute(sql, tuple(params))
                self._conn.commit()
                return cur
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                raise DatabaseError(f"database error: {exc}") from exc

    def _executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            try:
                self._conn.executemany(sql, rows)
                self._conn.commit()
            except sqlite3.Error as exc:  # pragma: no cover
                raise DatabaseError(f"database error: {exc}") from exc

    def query(self, sql: str, params: Iterable = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable = ()) -> int:
        cur = self._execute(sql, params)
        return cur.rowcount

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self._executemany(sql, rows)

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
