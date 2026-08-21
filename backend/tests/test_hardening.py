"""Hardening regression tests: dates, pagination, dedup methods, state machine,
path validation, score normalization, config."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.domain.models import Candidate, JobStatus
from app.exceptions import ConflictError, ValidationError
from app.infrastructure.repositories.job_repository import VALID_TRANSITIONS
from app.utils import (
    date_range_condition,
    ensure_within,
    sanitize_filename,
    validate_date_bound,
    validate_id,
)


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------
def test_date_only_upper_bound_is_exclusive():
    clause, params = date_range_condition("2026-08-01", "2026-08-17")
    assert "uploaded_at >=" in clause
    assert "uploaded_at <" in clause  # exclusive upper bound
    assert params[1] == "2026-08-18T00:00:00+00:00"  # next midnight


def test_date_to_with_time_stays_inclusive():
    clause, params = date_range_condition(None, "2026-08-17T23:59:59+00:00")
    assert "uploaded_at <=" in clause
    assert params[0] == "2026-08-17T23:59:59+00:00"


def test_date_from_midnight_start():
    clause, params = date_range_condition("2026-08-17", None)
    assert params[0] == "2026-08-17T00:00:00+00:00"


def test_same_day_range_includes_whole_day():
    clause, params = date_range_condition("2026-08-17", "2026-08-17")
    assert params == ["2026-08-17T00:00:00+00:00", "2026-08-18T00:00:00+00:00"]


def test_validate_date_bound_rejects_garbage():
    assert validate_date_bound("2026-08-17") == "2026-08-17"
    assert validate_date_bound(None) is None
    with pytest.raises(ValidationError):
        validate_date_bound("not-a-date")


def test_end_of_day_record_is_included(container):
    from app.domain.models import Video

    repo = container.video_repo
    now = "2026-08-10T00:00:00+00:00"
    for vid, when in (
        ("a", "2026-08-17T00:00:00+00:00"),
        ("b", "2026-08-17T23:59:00+00:00"),   # end of day — must be included
        ("c", "2026-08-18T00:00:00+00:00"),   # next midnight — must be excluded
    ):
        repo.insert(Video(
            video_id=vid, filename=f"{vid}.mp4", original_filename=f"{vid}.mp4",
            path="media/x.mp4", size_bytes=1, uploaded_at=when,
            created_at=now, updated_at=now,
        ))
    ids = repo.ids_uploaded_between("2026-08-17", "2026-08-17")
    assert set(ids) == {"a", "b"}


# ---------------------------------------------------------------------------
# Pagination totals honour the same filters
# ---------------------------------------------------------------------------
def test_count_matches_list_filters(container):
    from app.domain.models import Video

    repo = container.video_repo
    now = "2026-08-10T00:00:00+00:00"
    for i, when in enumerate(("2026-08-01T00:00:00+00:00", "2026-08-10T00:00:00+00:00", "2026-08-20T00:00:00+00:00")):
        repo.insert(Video(
            video_id=str(i), filename=f"f{i}.mp4", original_filename=f"f{i}.mp4",
            path="media/x.mp4", size_bytes=1, uploaded_at=when,
            created_at=now, updated_at=now,
        ))
    listed = repo.list(date_from="2026-08-05", date_to="2026-08-15", limit=100)
    counted = repo.count(date_from="2026-08-05", date_to="2026-08-15")
    assert len(listed) == counted == 1
    assert listed[0].video_id == "1"


# ---------------------------------------------------------------------------
# Dedup methods / config
# ---------------------------------------------------------------------------
def test_dedup_methods_supported():
    from app.infrastructure.perceptual import (
        SUPPORTED_DEDUP_METHODS,
        is_embedding_method,
        make_hash_function,
        normalize_method,
    )

    assert normalize_method("PHASH") == "phash"
    assert is_embedding_method("embedding")
    assert not is_embedding_method("dhash")
    assert make_hash_function("dhash") is not None
    with pytest.raises(ValueError):
        normalize_method("bogus")
    assert set(SUPPORTED_DEDUP_METHODS) == {"phash", "dhash", "embedding", "none"}


def test_phash_and_dhash_are_deterministic_and_discriminative(tmp_path):
    import numpy as np

    from app.infrastructure.perceptual import dhash, hamming, phash

    x = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.fromarray(x, "L").save(a)
    Image.fromarray(x[:, ::-1], "L").save(b)  # horizontally flipped

    assert phash(a) == phash(a)
    assert dhash(a) == dhash(a)
    assert hamming(dhash(a), dhash(b)) > 10
    assert hamming(phash(a), phash(b)) > 10


def test_dedup_method_none_keeps_all():
    from app.infrastructure.perceptual import normalize_method

    assert normalize_method("none") == "none"


# ---------------------------------------------------------------------------
# Job state machine
# ---------------------------------------------------------------------------
def test_job_transitions_valid():
    assert VALID_TRANSITIONS[JobStatus.QUEUED.value] == {
        JobStatus.RUNNING.value, JobStatus.CANCELLED.value, JobStatus.FAILED.value,
    }
    assert JobStatus.COMPLETED.value in VALID_TRANSITIONS[JobStatus.RUNNING.value]
    assert JobStatus.CANCELLING.value in VALID_TRANSITIONS[JobStatus.RUNNING.value]
    assert VALID_TRANSITIONS[JobStatus.COMPLETED.value] == set()


def test_job_invalid_transition_rejected(container):
    from app.domain.models import Job

    repo = container.job_repo
    job = Job(job_id="j1", video_id="v1", status=JobStatus.COMPLETED.value)
    repo.insert(job)
    with pytest.raises(ConflictError):
        repo.transition("j1", JobStatus.COMPLETED.value, JobStatus.RUNNING.value)
    with pytest.raises(ConflictError):
        # wrong from-state
        repo.transition("j1", JobStatus.QUEUED.value, JobStatus.RUNNING.value)


def test_job_transition_cas(container):
    from app.domain.models import Job

    repo = container.job_repo
    repo.insert(Job(job_id="j2", video_id="v1", status=JobStatus.QUEUED.value))
    repo.transition("j2", JobStatus.QUEUED.value, JobStatus.RUNNING.value)
    assert repo.get("j2").status == JobStatus.RUNNING.value
    # second transition from QUEUED no longer matches
    with pytest.raises(ConflictError):
        repo.transition("j2", JobStatus.QUEUED.value, JobStatus.CANCELLED.value)


# ---------------------------------------------------------------------------
# Path / id validation
# ---------------------------------------------------------------------------
def test_validate_id():
    assert validate_id("abc123") == "abc123"
    assert validate_id("vid_fine_42_1000") == "vid_fine_42_1000"
    with pytest.raises(ValidationError):
        validate_id("../../etc/passwd")
    with pytest.raises(ValidationError):
        validate_id("a b")
    with pytest.raises(ValidationError):
        validate_id("")


def test_ensure_within_blocks_traversal_and_symlink(tmp_path):
    base = tmp_path / "root"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (base / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        ensure_within(base, "link", "secret.txt")
    with pytest.raises(ValueError):
        ensure_within(base, "..", "outside")


def test_sanitize_filename():
    assert sanitize_filename("../../evil.sh") == "evil.sh"
    assert sanitize_filename("my movie (1).mp4") == "my_movie_1_.mp4"
    assert sanitize_filename("") == "file"


def test_storage_resolve_in_blocks_escape(container):
    from app.exceptions import StorageError

    storage = container.storage
    # relative path with traversal must be rejected
    with pytest.raises(StorageError):
        storage.resolve_in(container.settings.media_dir, "../chroma/x")
    # a path inside frames dir resolves
    p = storage.resolve_in(container.settings.frames_dir, "frames/v1/f.jpg")
    assert p == (container.settings.frames_dir / "v1" / "f.jpg").resolve()


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------
def test_score_normalization_bounded():
    import numpy as np

    from app.application.search_service import SearchService

    cands = [
        Candidate(frame_id="a", video_id="v", timestamp_seconds=1, score=0.9, frame_path="", video_path=""),
        Candidate(frame_id="b", video_id="v", timestamp_seconds=2, score=0.5, frame_path="", video_path=""),
    ]
    SearchService._normalize_scores([[cands[0]], [cands[1]]])
    for c in cands:
        assert 0.0 < c.score < 1.0
        assert c.raw_score != 0.0


def test_fine_frame_id_deterministic():
    from app.application.search_service import fine_frame_id

    assert fine_frame_id("v", 0.25, 84.75) == fine_frame_id("v", 0.25, 84.75)
    assert fine_frame_id("v", 0.25, 84.75) != fine_frame_id("v", 0.5, 84.75)
    assert fine_frame_id("v", 0.25, 84.75) != fine_frame_id("v", 0.25, 84.80)
