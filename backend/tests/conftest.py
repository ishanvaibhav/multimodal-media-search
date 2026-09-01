"""Test fixtures — isolated SQLite database + dev-mode auth per test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Isolate configuration BEFORE any app import (settings are cached).
_TMP = Path(__file__).parent / "_testdata"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AUTH_MODE", "dev")

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import session as db_session


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh app + empty database per test."""
    db_url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    db_session.reset_engine_for_tests()

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    db_session.reset_engine_for_tests()
    get_settings.cache_clear()


def bearer(email: str) -> dict[str, str]:
    """Dev-mode token header: identity only; role comes from the DB record."""
    return {"Authorization": f"Bearer dev:{email}"}


@pytest.fixture()
def admin_headers(client):
    """First authenticated user in dev mode becomes ADMIN (bootstrap rule)."""
    r = client.get("/api/auth/me", headers=bearer("admin@example.com"))
    assert r.status_code == 200, r.text
    return bearer("admin@example.com")
