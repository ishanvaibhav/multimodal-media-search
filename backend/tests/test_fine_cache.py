"""Fine-cache interval coverage, atomicity, concurrency and config-invalidation
regression tests."""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app.domain.models import Job, Video
from app.infrastructure.repositories.fine_cache_repository import compute_gaps
from app.utils import now_iso


def _index_video(container, video_id: str, path: Path, duration: float) -> None:
    """Copy a real video into the container and run its indexing job to
    completion (coarse frames + vectors)."""
    dest = container.settings.media_dir / f"{video_id}.mp4"
    shutil.copyfile(path, dest)
    container.video_repo.insert(Video(
        video_id=video_id, filename=f"{video_id}.mp4", original_filename="v.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        duration_seconds=duration, status="queued", uploaded_at=now_iso(),
        created_at=now_iso(), updated_at=now_iso(),
    ))
    job = Job(job_id=f"j_{video_id}", video_id=video_id)
    container.job_repo.insert(job)
    container.indexing_service.run_job(job, lambda: False)
    assert container.video_repo.get(video_id).status == "ready"


def _make_tiny_video(path: Path, color: str, seconds: int) -> Path:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"color=c={color}:s=160x120:r=10:d={seconds}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip("ffmpeg unavailable")
    return path


# ---------------------------------------------------------------------------
# Coverage algorithm (pure)
# ---------------------------------------------------------------------------
def test_coverage_full_adjacent():
    cached = [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)]
    assert compute_gaps(cached, 0.0, 15.0) == []


def test_coverage_disjoint_missing_middle():
    cached = [(0.0, 5.0), (10.0, 15.0)]
    assert compute_gaps(cached, 0.0, 15.0) == [(5.0, 10.0)]


def test_coverage_overlapping_intervals():
    cached = [(0.0, 6.0), (4.0, 12.0), (11.0, 15.0)]
    assert compute_gaps(cached, 0.0, 15.0) == []


def test_coverage_partial_edges():
    cached = [(0.0, 8.0), (8.0, 16.0)]
    assert compute_gaps(cached, 4.0, 20.0) == [(16.0, 20.0)]
    assert compute_gaps(cached, 0.0, 10.0) == []


def test_coverage_outside_window_ignored():
    cached = [(100.0, 200.0)]
    assert compute_gaps(cached, 0.0, 5.0) == [(0.0, 5.0)]


def test_coverage_empty_cache():
    assert compute_gaps([], 2.0, 8.0) == [(2.0, 8.0)]


# ---------------------------------------------------------------------------
# Repository coverage end-to-end
# ---------------------------------------------------------------------------
def test_repo_coverage_disjoint_not_merged(container):
    repo = container.fine_cache_repo
    repo.add_interval("v", 250, 0.0, 5.0, 20, 21, "fine-v1")
    repo.add_interval("v", 250, 10.0, 15.0, 20, 21, "fine-v1")
    covered, gaps = repo.coverage("v", 250, "fine-v1", 0.0, 15.0)
    assert covered is False
    assert gaps == [(5.0, 10.0)]


def test_repo_coverage_full_when_all_intervals_present(container):
    repo = container.fine_cache_repo
    repo.add_interval("v", 250, 0.0, 5.0, 20, 21, "fine-v1")
    repo.add_interval("v", 250, 5.0, 10.0, 20, 21, "fine-v1")
    repo.add_interval("v", 250, 10.0, 15.0, 20, 21, "fine-v1")
    covered, gaps = repo.coverage("v", 250, "fine-v1", 0.0, 15.0)
    assert covered is True and gaps == []


def test_repo_coverage_version_mismatch_invalidates(container):
    repo = container.fine_cache_repo
    repo.add_interval("v", 250, 0.0, 15.0, 60, 61, "fine-v1")
    # a different extraction version must NOT be treated as covering the window
    covered, gaps = repo.coverage("v", 250, "fine-v9", 0.0, 15.0)
    assert covered is False
    assert gaps == [(0.0, 15.0)]


def test_repo_coverage_interval_mismatch_invalidates(container):
    repo = container.fine_cache_repo
    repo.add_interval("v", 250, 0.0, 15.0, 60, 61, "fine-v1")
    covered, _ = repo.coverage("v", 500, "fine-v1", 0.0, 15.0)
    assert covered is False


# ---------------------------------------------------------------------------
# Concurrency: two searches must not both extract the same window
# ---------------------------------------------------------------------------
def test_concurrent_fine_search_single_extraction(container, tmp_path):
    """Two concurrent requests for the same window => one extraction."""
    vid_path = _make_tiny_video(tmp_path / "v.mp4", "0xff3333", 8)
    _index_video(container, "cv", vid_path, duration=8.0)

    container.settings.fine_search_max_frames = 200
    container.settings.fine_search_window_seconds = 4.0
    container.settings.fine_frame_interval_seconds = 0.5
    container.settings.fine_search_concurrency = 4

    extract_calls = {"n": 0}
    orig = container.ffmpeg.extract_frames_range

    def counting_extract(*args, **kwargs):
        extract_calls["n"] += 1
        return orig(*args, **kwargs)

    container.ffmpeg.extract_frames_range = counting_extract

    results = []
    lock = threading.Lock()

    def run_search():
        r = container.search_service.search("red", {"mode": "accurate", "fine_search": True})
        with lock:
            results.append(r)

    ts = [threading.Thread(target=run_search) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    # both searches produced results
    assert all(r["results"] for r in results)
    # only ONE window extraction happened (the second request used the cache)
    assert extract_calls["n"] == 1
    # and a complete manifest interval now exists
    assert container.fine_cache_repo.all_intervals()


def test_fine_cache_is_video_scoped(container, tmp_path):
    """Fine-search frames/manifests must never leak across videos."""
    v1 = _make_tiny_video(tmp_path / "a.mp4", "0xff3333", 6)
    v2 = _make_tiny_video(tmp_path / "b.mp4", "0x3333ff", 6)
    _index_video(container, "fa", v1, duration=6.0)
    _index_video(container, "fb", v2, duration=6.0)

    container.settings.fine_search_max_frames = 100
    container.settings.fine_search_window_seconds = 3.0
    container.settings.fine_frame_interval_seconds = 0.5

    # fine-search only video A
    r = container.search_service.search(
        "red", {"mode": "accurate", "fine_search": True, "video_ids": ["fa"]}
    )
    assert r["results"]

    # video B has no fine-cache frames and no manifest rows
    assert container.frame_repo.count_for_video("fb", "fine_cache") == 0
    manifests = [m for m in container.fine_cache_repo.all_intervals() if m["video_id"] == "fb"]
    assert manifests == []


def test_maintenance_blocks_fine_cache_writes(container, tmp_path):
    """Global maintenance must prevent fine-search cache mutations."""
    vid_path = _make_tiny_video(tmp_path / "v.mp4", "0x3333ff", 4)
    _index_video(container, "mv", vid_path, duration=4.0)

    container.gate.start()
    try:
        # accurate search runs but fine search must be skipped (no cache writes)
        r = container.search_service.search("blue", {"mode": "accurate", "fine_search": True})
        assert r["results"]  # coarse results still returned
        assert container.fine_cache_repo.all_intervals() == []
    finally:
        container.gate.stop()
