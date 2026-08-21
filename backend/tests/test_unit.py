"""Pure unit tests: no FFmpeg or ML model required."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.application.search_service import temporal_group
from app.domain.models import Candidate
from app.exceptions import UploadError, ValidationError
from app.utils import (
    ensure_within,
    format_hms,
    human_size,
    parse_duration,
    parse_fraction,
    sanitize_filename,
)


# --- filename sanitization -----------------------------------------------
def test_sanitize_strips_traversal():
    assert sanitize_filename("../../etc/passwd.mp4") == "passwd.mp4"
    assert sanitize_filename("..\\..\\windows\\system32\\x.avi") == "x.avi"
    assert ".." not in sanitize_filename("..")
    assert sanitize_filename("..") == "file"


def test_sanitize_replaces_unsafe_chars():
    assert sanitize_filename("my video (1).mp4") == "my_video_1_.mp4"
    assert sanitize_filename("") == "file"


# --- formatting helpers ---------------------------------------------------
def test_format_hms():
    assert format_hms(0) == "00:00"
    assert format_hms(84.6) == "01:25"
    assert format_hms(3600 + 60 + 8) == "01:01:08"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.00 KB"
    assert human_size(1024 ** 3) == "1.00 GB"


def test_parse_fraction_and_duration():
    assert parse_fraction("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert parse_fraction("0/0") is None
    assert parse_duration("01:32:14.12") == pytest.approx(5534.12, abs=0.01)
    assert parse_duration("123.45") == pytest.approx(123.45)


# --- path traversal guard --------------------------------------------------
def test_ensure_within():
    base = Path("/tmp/base")
    assert ensure_within(base, "a", "b").is_relative_to(base)
    with pytest.raises(ValueError):
        ensure_within(base, "..", "etc")


# --- temporal grouping ------------------------------------------------------
def _cand(ts: float, score: float) -> Candidate:
    return Candidate(
        frame_id=f"f_{ts}", video_id="v", timestamp_seconds=ts, score=score,
        frame_path="", video_path="",
    )


def test_temporal_grouping():
    hits = [
        _cand(391, 0.7), _cand(84, 0.9), _cand(86, 0.85), _cand(88, 0.8),
        _cand(154, 0.6), _cand(156, 0.65),
    ]
    events = temporal_group(hits, window=5, max_per_event=3)
    assert len(events) == 3
    # events sorted by best score desc: 84..88 (0.9), 391 (0.7), 154..156 (0.65)
    assert events[0][0].timestamp_seconds == 84
    # representative of each event is its highest-score member
    assert [e[0].timestamp_seconds for e in events] == [84, 391, 156]
    assert len(events[0]) == 3


def test_temporal_grouping_disabled_via_single_groups():
    hits = [_cand(84, 0.9), _cand(86, 0.85)]
    events = temporal_group(hits, window=0.1, max_per_event=2)
    assert len(events) == 2


def test_temporal_grouping_never_merges_across_videos():
    # Video A @10s and Video B @11s must never form one event.
    hits = [
        _cand(10, 0.9),
        _cand(11, 0.8),
    ]
    hits[0].video_id = "videoA"
    hits[1].video_id = "videoB"
    events = temporal_group(hits, window=60, max_per_event=5)
    assert len(events) == 2
    assert {e[0].video_id for e in events} == {"videoA", "videoB"}

    # same video, same window -> single event
    a1, a2 = _cand(10, 0.9), _cand(12, 0.8)
    a1.video_id = a2.video_id = "videoA"
    events = temporal_group([a1, a2], window=60, max_per_event=5)
    assert len(events) == 1


# --- perceptual dedup --------------------------------------------------------
def test_dhash_identical_images():
    import numpy as np

    from app.infrastructure.perceptual import dhash as _dhash
    from app.infrastructure.perceptual import hamming as _hamming

    x = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (64, 1))  # left dark -> right light
    img = Image.fromarray(x, "L")
    p1 = Path("/tmp/_dhash_a.png")
    p2 = Path("/tmp/_dhash_b.png")
    img.save(p1)
    img.save(p2)
    h1, h2 = _dhash(p1), _dhash(p2)
    assert _hamming(h1, h2) == 0

    flipped = Image.fromarray(x[:, ::-1], "L")  # left light -> right dark
    flipped.save(p2)
    h3 = _dhash(p2)
    assert _hamming(h1, h3) > 10


# --- upload validation ---------------------------------------------------------
def test_upload_init_rejects_bad_extension(container):
    with pytest.raises(ValidationError):
        container.upload_service.init("evil.exe", 1024)


def test_upload_init_rejects_oversize(container):
    with pytest.raises(UploadError):
        container.upload_service.init("big.mp4", container.settings.max_upload_size_bytes + 1)


def test_upload_init_ok(container):
    upload = container.upload_service.init("movie.mp4", 5 * 1024 * 1024)
    assert upload.total_chunks == 5  # chunk_size_mb=1 in test settings
    assert upload.status == "uploading"


# --- date filtering (repository) ---------------------------------------------
def test_ids_uploaded_between(container):
    from app.domain.models import Video

    repo = container.video_repo
    now = "2026-08-10T00:00:00+00:00"
    for vid, when in (
        ("a", "2026-08-01T00:00:00+00:00"),
        ("b", "2026-08-10T00:00:00+00:00"),
        ("c", "2026-08-20T00:00:00+00:00"),
    ):
        repo.insert(Video(
            video_id=vid, filename=f"{vid}.mp4", original_filename=f"{vid}.mp4",
            path="/tmp/x.mp4", size_bytes=1, uploaded_at=when,
            created_at=now, updated_at=now,
        ))
    ids = repo.ids_uploaded_between(
        "2026-08-05T00:00:00+00:00", "2026-08-15T00:00:00+00:00"
    )
    assert set(ids) == {"b"}
