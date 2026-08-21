"""Concurrency and lifecycle-coordination tests."""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest


def _make_video(path: Path, seconds: int = 6, hue: str = "red") -> Path:
    ffmpeg = "ffmpeg"
    color = {"red": "0xff3333", "blue": "0x3333ff", "green": "0x33ff33"}[hue]
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {proc.stderr[-200:]}")
    return path


def _run_job_in_thread(container, job, gate=None):
    """Run a job the way the worker does: holding the per-video lock."""
    if container.job_repo.get(job.job_id) is None:
        container.job_repo.insert(job)

    def cancel_check():
        return False

    def target():
        with container.coordinator.hold(job.video_id):
            container.indexing_service.run_job(job, cancel_check)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
def test_two_concurrent_indexing_jobs_are_isolated(container, tmp_path):
    from app.domain.models import Job

    v1 = _make_video(tmp_path / "red.mp4", seconds=6, hue="red")
    v2 = _make_video(tmp_path / "blue.mp4", seconds=6, hue="blue")

    for vid, path in (("v1", v1), ("v2", v2)):
        import shutil
        from app.domain.models import Video
        from app.utils import now_iso

        dest = container.settings.media_dir / f"{vid}.mp4"
        shutil.copyfile(path, dest)
        container.video_repo.insert(Video(
            video_id=vid, filename=f"{vid}.mp4", original_filename=f"{vid}.mp4",
            path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
            status="queued", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
        ))

    job1 = Job(job_id="j1", video_id="v1")
    job2 = Job(job_id="j2", video_id="v2")
    container.job_repo.insert(job1)
    container.job_repo.insert(job2)

    t1 = _run_job_in_thread(container, job1)
    t2 = _run_job_in_thread(container, job2)
    t1.join(timeout=120)
    t2.join(timeout=120)

    assert container.job_repo.get("j1").status == "completed"
    assert container.job_repo.get("j2").status == "completed"
    assert not t1.is_alive() and not t2.is_alive()

    # isolation: every frame references exactly its own video
    f1 = container.frame_repo.list_for_video("v1")
    f2 = container.frame_repo.list_for_video("v2")
    assert len(f1) > 0 and len(f2) > 0
    assert all(f.video_id == "v1" for f in f1)
    assert all(f.video_id == "v2" for f in f2)
    # vectors: counts sum correctly and no cross-video ids
    ids1 = container.vectorstore.all_ids_for_video("v1")
    ids2 = container.vectorstore.all_ids_for_video("v2")
    assert ids1 and ids2 and not (ids1 & ids2)


def test_delete_while_indexing_does_not_resurrect(container, tmp_path):
    import shutil

    from app.domain.models import Job, Video
    from app.utils import now_iso

    src = _make_video(tmp_path / "g.mp4", seconds=8, hue="green")
    vid = "vd"
    dest = container.settings.media_dir / f"{vid}.mp4"
    shutil.copyfile(src, dest)
    container.video_repo.insert(Video(
        video_id=vid, filename=f"{vid}.mp4", original_filename="g.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        status="queued", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
    ))

    # start indexing in a thread whose cancel_check honours DB cancellation
    job = Job(job_id="jd", video_id=vid)
    container.job_repo.insert(job)

    started = threading.Event()
    def cancel_check():
        started.set()
        j = container.job_repo.get("jd")
        return j.status in ("cancelling", "cancelled")

    def target():
        with container.coordinator.hold(vid):
            container.indexing_service.run_job(job, cancel_check)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    started.wait(timeout=30)  # ensure indexing began

    # now delete while indexing is running
    result = container.media_service.delete(vid)
    t.join(timeout=60)
    assert result["deleted"]["video_file"] == 1
    assert container.video_repo.get(vid) is None
    assert container.vectorstore.all_ids_for_video(vid) == set()
    assert container.frame_repo.count_for_video(vid) == 0
    # job reached a terminal state
    assert container.job_repo.get("jd").status in ("cancelled", "failed", "completed")


def test_repeated_fine_search_is_idempotent(container, tmp_path):
    import shutil

    from app.domain.models import Job, Video
    from app.utils import now_iso

    src = _make_video(tmp_path / "r.mp4", seconds=10, hue="red")
    vid = "vr"
    dest = container.settings.media_dir / f"{vid}.mp4"
    shutil.copyfile(src, dest)
    container.video_repo.insert(Video(
        video_id=vid, filename=f"{vid}.mp4", original_filename="r.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        duration_seconds=10.0, status="queued", uploaded_at=now_iso(),
        created_at=now_iso(), updated_at=now_iso(),
    ))

    # index the video first (coarse frames)
    _run_job_in_thread(container, Job(job_id="jr2", video_id=vid)).join(timeout=120)
    assert container.video_repo.get(vid).status == "ready"

    # run fine search twice with identical parameters
    r1 = container.search_service.search("a red thing", {"mode": "accurate", "fine_search": True})
    count_after_1 = container.frame_repo.count_for_video(vid)
    r2 = container.search_service.search("a red thing", {"mode": "accurate", "fine_search": True})
    count_after_2 = container.frame_repo.count_for_video(vid)

    assert r1["results"] or r2["results"]  # search succeeded
    # no unbounded growth: second identical search must not double the rows
    assert count_after_1 == count_after_2
