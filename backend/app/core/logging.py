"""Structured logging (plan §46–§47).

Every log record carries a ``request_id`` when one is bound for the current
context, so an admin can trace: request → API → worker → database with a
single identifier.
"""

from __future__ import annotations

import contextvars
import logging
import sys
from datetime import UTC, datetime

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def bind_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class _JsonishFormatter(logging.Formatter):
    """Single-line, key=value style formatter.

    Keeps local development readable while staying trivially parseable by log
    shippers. Swap for a full JSON encoder behind LOG_FORMAT=json later.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        base = (
            f"{ts} level={record.levelname} logger={record.name} "
            f"request_id={getattr(record, 'request_id', '-')} msg={record.getMessage()!r}"
        )
        if record.exc_info:
            base += " exc=" + self.formatException(record.exc_info).replace("\n", " | ")
        return base


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonishFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Quiet chatty libraries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True
