"""Shared helpers for evaluation tests (demo video + indexing)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root (contains test_images/ and backend/)


def build_demo_video(tmp_path: Path) -> Path:
    """Concatenate test_images into a deterministic demo video:
    dog 0-8, cat 8-16, car 16-24, person 24-32, dog 32-40, car 40-48."""
    images = REPO_ROOT / "test_images"
    needed = ["dog.jpg", "cat.jpg", "car.jpg", "person.jpg"]
    if not all((images / n).exists() for n in needed):
        pytest.skip("test_images/ not populated (run from the repo root)")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")

    order = ["dog.jpg", "cat.jpg", "car.jpg", "person.jpg", "dog.jpg", "car.jpg"]
    segments = []
    for i, name in enumerate(order):
        seg = tmp_path / f"seg{i}.mp4"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-loop", "1", "-i", str(images / name), "-t", "8",
             "-vf", "scale=320:240:force_original_aspect_ratio=decrease,"
                    "pad=320:240:(ow-iw)/2:(oh-ih)/2:color=black",
             "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            pytest.skip(f"ffmpeg segment failed: {proc.stderr[-200:]}")
        segments.append(seg)
    with open(tmp_path / "list.txt", "w") as f:
        for s in segments:
            f.write(f"file '{s.name}'\n")
    out = tmp_path / "demo.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
         "-safe", "0", "-i", str(tmp_path / "list.txt"), "-c", "copy", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg concat failed: {proc.stderr[-200:]}")
    return out


def index_demo_video(container, path: Path, video_id: str = "evaldemo") -> None:
    """Copy the demo video into the container and run its indexing job."""
    from app.domain.models import Job, Video
    from app.utils import now_iso

    dest = container.settings.media_dir / "demo.mp4"
    shutil.copyfile(path, dest)
    container.video_repo.insert(Video(
        video_id=video_id, filename="demo.mp4", original_filename="demo.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        duration_seconds=48.0, status="queued", uploaded_at=now_iso(),
        created_at=now_iso(), updated_at=now_iso(),
    ))
    job = Job(job_id=f"j_{video_id}", video_id=video_id)
    container.job_repo.insert(job)
    container.indexing_service.run_job(job, lambda: False)
    assert container.video_repo.get(video_id).status == "ready"
