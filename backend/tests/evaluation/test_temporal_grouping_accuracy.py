"""Temporal-grouping accuracy tests (synthetic, no ML)."""
from __future__ import annotations

from app.application.search_service import temporal_group
from app.domain.models import Candidate


def _c(video: str, ts: float, score: float = 0.9) -> Candidate:
    return Candidate(
        frame_id=f"{video}_{ts}", video_id=video, timestamp_seconds=ts,
        score=score, frame_path="", video_path="",
    )


def _starts(events):
    return [(e[0].video_id, e[0].timestamp_seconds) for e in events]


def test_same_video_nearby_frames_group_together():
    events = temporal_group(
        [_c("A", 1.0), _c("A", 2.0), _c("A", 3.0)], window=5, max_per_event=5
    )
    assert len(events) == 1
    assert len(events[0]) == 3


def test_same_video_distant_frames_split():
    events = temporal_group(
        [_c("A", 1.0), _c("A", 20.0)], window=5, max_per_event=5
    )
    assert len(events) == 2


def test_different_videos_never_merge():
    events = temporal_group(
        [_c("A", 10.0), _c("B", 11.0)], window=60, max_per_event=5
    )
    assert len(events) == 2
    assert {e[0].video_id for e in events} == {"A", "B"}


def test_duplicate_timestamps_handled():
    events = temporal_group(
        [_c("A", 1.0, 0.9), _c("A", 1.0, 0.8)], window=5, max_per_event=5
    )
    # duplicates are grouped; the representative is the highest-score one
    assert len(events) == 1
    assert len(events[0]) == 2
    assert events[0][0].score == 0.9


def test_out_of_order_input_is_deterministic():
    a = [_c("A", 3.0), _c("A", 1.0), _c("A", 2.0)]
    b = [_c("A", 1.0), _c("A", 2.0), _c("A", 3.0)]
    assert _starts(temporal_group(a, 5, 5)) == _starts(temporal_group(b, 5, 5))


def test_missing_timestamps_do_not_bridge_large_gaps():
    # frames at 0 and 100 with a 5s window must never group across the gap
    events = temporal_group([_c("A", 0.0), _c("A", 100.0)], window=5, max_per_event=5)
    assert len(events) == 2


def test_video_boundary_closes_event_even_within_window():
    # A@10, B@12 within a huge window: still two events
    events = temporal_group([_c("A", 10.0), _c("B", 12.0)], window=100, max_per_event=5)
    assert len(events) == 2
