"""P1 regression tests: fine-search budgets, metadata filter parity,
datetime normalization, query limits, frame_type, cache completeness."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import Candidate
from app.exceptions import ValidationError
from app.utils import validate_date_bound


# ---------------------------------------------------------------------------
# Fine-search budgets (pure logic via monkeypatching)
# ---------------------------------------------------------------------------
def _events(video_ids, ts=10.0):
    evs = []
    for vid in video_ids:
        c = Candidate(
            frame_id=f"{vid}_0", video_id=vid, timestamp_seconds=ts,
            score=0.9, frame_path="", video_path="",
        )
        evs.append([c])
    return evs


def _seed_videos(container, ids):
    from app.domain.models import Video

    now = "2026-08-10T00:00:00+00:00"
    for vid in ids:
        container.video_repo.insert(Video(
            video_id=vid, filename=f"{vid}.mp4", original_filename=f"{vid}.mp4",
            path=f"media/{vid}.mp4", size_bytes=1, duration_seconds=100.0,
            status="ready", uploaded_at=now, created_at=now, updated_at=now,
        ))


def test_fine_search_video_budget(container):
    svc = container.search_service
    svc.settings.fine_search_max_videos = 2
    svc.settings.fine_search_max_frames = 1000
    svc.settings.fine_search_max_events = 100
    svc.settings.fine_search_max_timestamps = 100
    svc.settings.fine_search_window_seconds = 2.0
    svc.settings.fine_frame_interval_seconds = 0.5
    _seed_videos(container, ["A", "B", "C", "D"])

    calls: list[str] = []

    def fake_window(self, video, q_emb, lo, hi, interval, expected):
        calls.append(video.video_id)
        c = Candidate(
            frame_id=f"{video.video_id}_best", video_id=video.video_id,
            timestamp_seconds=lo, score=0.9, raw_score=0.9,
            frame_path="", video_path=video.path,
        )
        return c

    import types

    svc._fine_search_window = types.MethodType(fake_window, svc)
    # 4 distinct videos, budget = 2 -> only first 2 may be fine-searched;
    # extra events from those same videos are allowed.
    events = _events(["A", "B", "C", "D"]) + _events(["A", "B"])
    svc._fine_search("q", None, events, {})
    distinct = sorted(set(calls))
    assert distinct == ["A", "B"]
    assert "C" not in calls and "D" not in calls


def test_fine_search_global_frame_budget(container):
    svc = container.search_service
    svc.settings.fine_search_max_videos = 10
    svc.settings.fine_search_max_frames = 100
    svc.settings.fine_search_max_events = 100
    svc.settings.fine_search_max_timestamps = 100
    svc.settings.fine_search_window_seconds = 2.0
    svc.settings.fine_frame_interval_seconds = 0.25
    _seed_videos(container, ["A", "B", "C", "D", "E", "F", "G", "H"])

    total = {"frames": 0}

    def fake_window(self, video, q_emb, lo, hi, interval, expected):
        total["frames"] += expected
        return Candidate(
            frame_id=f"{video.video_id}_b", video_id=video.video_id,
            timestamp_seconds=lo, score=0.9, raw_score=0.9,
            frame_path="", video_path="",
        )

    import types

    svc._fine_search_window = types.MethodType(fake_window, svc)
    events = _events(["A", "B", "C", "D", "E", "F", "G", "H"])
    svc._fine_search("q", None, events, {})
    # total frames consumed across ALL windows never exceeds the global budget
    assert total["frames"] <= svc.settings.fine_search_max_frames


# ---------------------------------------------------------------------------
# Metadata search filter parity
# ---------------------------------------------------------------------------
def test_metadata_search_honors_filters(container):
    from app.domain.models import Video

    repo = container.video_repo
    now = "2026-08-10T00:00:00+00:00"
    repo.insert(Video(
        video_id="m1", filename="m1.mp4", original_filename="cat video.mp4",
        path="media/m1.mp4", size_bytes=1, uploaded_at="2026-08-01T00:00:00+00:00",
        status="ready", created_at=now, updated_at=now,
    ))
    repo.insert(Video(
        video_id="m2", filename="m2.mp4", original_filename="cat video.mp4",
        path="media/m2.mp4", size_bytes=1, uploaded_at="2026-08-20T00:00:00+00:00",
        status="ready", created_at=now, updated_at=now,
    ))

    # date filter excludes m2 (uploaded after the range)
    res = container.search_service.search(
        "cat", {"mode": "metadata", "date_from": "2026-08-01", "date_to": "2026-08-10"}
    )
    ids = {r["video_id"] for r in res["results"]}
    assert ids == {"m1"}

    # explicit video_ids filter
    res = container.search_service.search(
        "cat", {"mode": "metadata", "video_ids": ["m2"]}
    )
    assert {r["video_id"] for r in res["results"]} == {"m2"}


# ---------------------------------------------------------------------------
# Datetime normalization
# ---------------------------------------------------------------------------
def test_datetime_normalization():
    # date-only passes through
    assert validate_date_bound("2026-08-17") == "2026-08-17"
    # timezone-aware offset is normalized to UTC
    out = validate_date_bound("2026-08-17T10:00:00+05:30")
    assert out == "2026-08-17T04:30:00+00:00"
    out = validate_date_bound("2026-08-17T00:00:00-05:00")
    assert out == "2026-08-17T05:00:00+00:00"
    # Z suffix accepted
    assert validate_date_bound("2026-08-17T00:00:00Z") == "2026-08-17T00:00:00+00:00"


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        validate_date_bound("2026-08-17T10:00:00")  # no offset


def test_query_length_limit(container):
    svc = container.search_service
    svc.settings.max_query_length = 10
    with pytest.raises(ValidationError):
        svc.search("x" * 11, {"mode": "fast"})
    # within limit works (may return empty results, but no ValidationError)
    res = svc.search("ok query", {"mode": "fast"})
    assert res["query"] == "ok query"


# ---------------------------------------------------------------------------
# frame_type explicit classification
# ---------------------------------------------------------------------------
def test_frames_have_explicit_frame_type(container, sample_video):
    import shutil
    from app.domain.models import Frame, FrameType

    dest = container.settings.media_dir / "ft.mp4"
    shutil.copyfile(sample_video, dest)
    _seed_videos(container, ["ft"])
    # insert a coarse and a fine frame directly
    container.frame_repo.upsert(Frame(
        frame_id="ft_000001", video_id="ft", timestamp_seconds=1.0,
        frame_path="frames/ft/a.jpg", frame_type=FrameType.COARSE.value,
    ))
    container.frame_repo.upsert(Frame(
        frame_id="ft_000002", video_id="ft", timestamp_seconds=1.25,
        frame_path="frames/ft/b.jpg", frame_type=FrameType.FINE_CACHE.value,
    ))
    assert container.frame_repo.count_for_video("ft", "coarse") == 1
    assert container.frame_repo.count_for_video("ft", "fine_cache") == 1
    got = container.frame_repo.get("ft_000002")
    assert got.frame_type == "fine_cache"


# ---------------------------------------------------------------------------
# Fine-cache interval manifest
# ---------------------------------------------------------------------------
def test_fine_cache_interval_roundtrip(container):
    repo = container.fine_cache_repo
    repo.add_interval("v1", 250, 0.0, 4.0, frame_count=16, expected_count=17,
                      extraction_version="fine-v1")
    repo.add_interval("v1", 250, 10.0, 15.0, frame_count=20, expected_count=21,
                      extraction_version="fine-v1")
    intervals = repo.intervals_for("v1", 250, "fine-v1")
    assert len(intervals) == 2
    # disjoint intervals are NOT merged into a continuous range
    assert (0.0, 4.0) in intervals and (10.0, 15.0) in intervals
    repo.invalidate("v1")
    assert repo.intervals_for("v1", 250, "fine-v1") == []


# ---------------------------------------------------------------------------
# Migration: existing DB upgrades to latest schema
# ---------------------------------------------------------------------------
def test_migration_reaches_latest_schema(tmp_path):
    from app.infrastructure.database import Database

    db = Database(tmp_path / "app.db")
    assert db.schema_version() >= 4
    assert db.column_exists("frames", "frame_type")
    assert db.column_exists("videos", "needs_reconciliation")
    assert db.column_exists("uploads", "result_video_id")
    assert db.column_exists("model_info", "is_active")
    db.close()
