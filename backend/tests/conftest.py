"""Shared pytest fixtures.

Tests run against isolated temporary data directories so they never touch a
developer's real index. The embedding backend can be overridden with the
EMBEDDING_BACKEND env var (default: deterministic for fast, offline tests).

Any code path that falls back to the *default* Settings (rather than the
per-test ``settings`` fixture) is redirected into the SYSTEM TEMP dir — never
into the repository — so a test run can never create or mutate ``data/``,
``_testdata/`` or any other repo-relative runtime artifact.
"""
from __future__ import annotations

import os
import tempfile

_TEST_ROOT = os.path.join(tempfile.gettempdir(), "media_search_test_data")

os.environ.setdefault("EMBEDDING_BACKEND", "deterministic")
os.environ.setdefault("DATA_DIR", _TEST_ROOT)
os.environ.setdefault("CHROMA_PATH", os.path.join(_TEST_ROOT, "chroma"))

import shutil  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def settings(tmp_path):
    from app.config import Settings

    # point the app at an isolated, per-test data dir (unique per test so the
    # persistent ChromaDB handle is never pointed at a deleted DB file)
    data_dir = tmp_path / "data"
    s = Settings(
        data_dir=str(data_dir),
        chroma_path=str(data_dir / "chroma"),
        embedding_backend=os.environ.get("EMBEDDING_BACKEND", "deterministic"),
        frame_interval_seconds=1.0,
        fine_frame_interval_seconds=0.5,
        temporal_group_window_seconds=3.0,
        embedding_batch_size=8,
        chunk_size_mb=1,
        max_upload_size_gb=5,
        max_concurrent_jobs=1,
        _env_file=None,
    )
    s.ensure_dirs()
    yield s
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture()
def container(settings):
    from app.container import build_container

    return build_container(settings)


@pytest.fixture()
def sample_video(settings):
    """Generate a small synthetic test video with FFmpeg (if available)."""
    from app.infrastructure.ffmpeg import FFmpegService

    ffmpeg = FFmpegService(settings)
    if not ffmpeg.ffmpeg_available():
        pytest.skip("FFmpeg not available")
    out = settings.temp_dir / "sample.mp4"
    import subprocess

    cmd = [
        str(ffmpeg.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=12",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"could not generate test video: {proc.stderr[-200:]}")
    return out
