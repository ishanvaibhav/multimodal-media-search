"""Production fail-fast invariants (plan §70 — rules 7 & 8)."""

import pytest

from app.core.config import Settings
from app.db.models import JOB_TRANSITIONS, JobStatus


def test_production_requires_firebase_auth():
    with pytest.raises(ValueError, match="AUTH_MODE"):
        Settings(APP_ENV="production", AUTH_MODE="dev", _env_file=None)


def test_production_requires_project_id():
    with pytest.raises(ValueError, match="FIREBASE_PROJECT_ID"):
        Settings(APP_ENV="production", AUTH_MODE="firebase", _env_file=None)


def test_production_forbids_sqlite():
    with pytest.raises(ValueError, match="SQLite"):
        Settings(
            APP_ENV="production",
            AUTH_MODE="firebase",
            FIREBASE_PROJECT_ID="demo",
            DATABASE_URL="sqlite:///x.db",
            _env_file=None,
        )


def test_production_valid_config_accepted():
    s = Settings(
        APP_ENV="production",
        AUTH_MODE="firebase",
        FIREBASE_PROJECT_ID="demo",
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/aimedia",
        _env_file=None,
    )
    assert s.is_production


def test_job_state_machine_has_no_arbitrary_transitions():
    # Terminal states are absorbing.
    for terminal in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED):
        assert JOB_TRANSITIONS[terminal] == frozenset()
    # All transitions point at declared statuses only.
    for src, targets in JOB_TRANSITIONS.items():
        assert all(isinstance(t, JobStatus) for t in targets), src
    # A job can never go back to QUEUED once RUNNING.
    assert JobStatus.QUEUED not in JOB_TRANSITIONS[JobStatus.RUNNING]
