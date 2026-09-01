"""Engine & session management (plan §4, §11)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if _engine is None:
        db_url = url or get_settings().DATABASE_URL
        if db_url.startswith("sqlite:///"):
            db_path = db_url[len("sqlite:///") :]
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(db_url, connect_args=_connect_args(db_url), pool_pre_ping=True)
        if db_url.startswith("sqlite"):
            # WAL + foreign keys for the local-dev SQLite file.
            @event.listens_for(_engine, "connect")
            def _sqlite_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def get_db() -> Iterator[Session]:
    """FastAPI dependency — one session per request, closed deterministically."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def reset_engine_for_tests() -> None:
    """Dispose cached engine/session (used by the test-suite between cases)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
