"""Structured, human-readable logging configuration."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "media_search"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level.upper())

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "backend.log", encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:  # pragma: no cover - logging must never crash startup
            root.warning("Could not create log file in %s", log_dir)

    _configured = True


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)
