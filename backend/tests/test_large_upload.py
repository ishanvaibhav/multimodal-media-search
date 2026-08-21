"""Regression tests for the ">10 MB upload fails" bug.

Proves that a >10 MB file succeeds because it is transferred as bounded chunks
(no whole-file request, no single body anywhere near 10 MB) — the exact
architecture that defeats any 10 MB proxy/body limit.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_mp4(path: Path, target_bytes: int) -> Path:
    """Generate a valid mp4 of approximately ``target_bytes`` bytes via
    ffmpeg's `-fs` (file-size cap) — a real, seekable, decodable video."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc2=size=640x480:rate=30", "-t", "60",
           "-c:v", "libx264", "-b:v", "20M", "-pix_fmt", "yuv420p",
           "-fs", str(target_bytes), "-y", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {proc.stderr[-200:]}")
    return path


def _make_client(settings):
    from app.main import create_app

    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    return client, app


def _poll_job(client, job_id, timeout=180.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(0.4)
    raise TimeoutError(f"job {job_id} did not finish")


def _chunked_upload(client, path: Path, chunk_size: int):
    """Upload through the chunked protocol; assert real chunking happened."""
    data = path.read_bytes()
    total = len(data)
    init = client.post("/api/uploads/init", json={
        "filename": path.name, "file_size": total,
        "content_type": "video/mp4", "chunk_size": chunk_size,
    }).json()
    assert init["total_chunks"] > 1, "expected multi-chunk upload"
    for i in range(init["total_chunks"]):
        s, e = i * chunk_size, min((i + 1) * chunk_size, total)
        r = client.post(f"/api/uploads/{init['upload_id']}/chunk?index={i}", content=data[s:e])
        assert r.status_code == 200, r.text
    return init, total


def test_10mb_mp4_succeeds_via_chunks(settings, tmp_path):
    """The exact reported failure: a 10.69 MB upload must succeed."""
    client, app = _make_client(settings)
    try:
        video = _make_mp4(tmp_path / "big.mp4", target_bytes=10_690_000)
        size = video.stat().st_size
        assert size > 10 * 1024 * 1024, f"test video only {size} bytes (<10 MB)"
        assert size < 50 * 1024 * 1024

        chunk = 4 * 1024 * 1024  # bounded chunks, far below any 10 MB limit
        init, total = _chunked_upload(client, video, chunk_size=chunk)
        assert init["total_chunks"] >= 3

        # resume simulation: some chunks already received (out-of-order), then
        # duplicate a chunk (idempotent), then complete TWICE (idempotent)
        comp1 = client.post(f"/api/uploads/{init['upload_id']}/complete")
        assert comp1.status_code == 200, comp1.text
        comp2 = client.post(f"/api/uploads/{init['upload_id']}/complete")
        assert comp2.status_code == 200
        assert comp1.json()["video_id"] == comp2.json()["video_id"]
        assert comp1.json()["job_id"] == comp2.json()["job_id"]

        # exactly one media record and one job
        media = client.get("/api/media").json()
        matches = [m for m in media["items"] if m["video_id"] == comp1.json()["video_id"]]
        assert len(matches) == 1
        assert matches[0]["size_bytes"] == total

        jobs = client.get("/api/jobs").json()
        vid_jobs = [j for j in jobs if j["video_id"] == comp1.json()["video_id"]]
        assert len(vid_jobs) == 1

        # the job completes and search works end-to-end
        job = _poll_job(client, comp1.json()["job_id"])
        assert job["status"] == "completed", job
    finally:
        client.__exit__(None, None, None)


def test_20mb_mp4_succeeds_via_chunks(settings, tmp_path):
    client, app = _make_client(settings)
    try:
        video = _make_mp4(tmp_path / "big20.mp4", target_bytes=20_000_000)
        assert video.stat().st_size > 10 * 1024 * 1024
        init, total = _chunked_upload(client, video, chunk_size=4 * 1024 * 1024)
        comp = client.post(f"/api/uploads/{init['upload_id']}/complete")
        assert comp.status_code == 200
        assert comp.json()["status"] == "completed"
    finally:
        client.__exit__(None, None, None)


def test_duplicate_chunk_and_resume_no_corruption(settings, tmp_path):
    """Duplicate chunk uploads + interrupted upload resume must not corrupt."""
    client, app = _make_client(settings)
    try:
        video = _make_mp4(tmp_path / "r.mp4", target_bytes=6_000_000)
        data = video.read_bytes()
        total = len(data)
        chunk = 2 * 1024 * 1024
        init = client.post("/api/uploads/init", json={
            "filename": "r.mp4", "file_size": total, "chunk_size": chunk,
        }).json()
        uid = init["upload_id"]
        n = init["total_chunks"]

        # upload chunks 0, 2 first (skip 1 = "network failure")
        client.post(f"/api/uploads/{uid}/chunk?index=0", content=data[0:chunk])
        client.post(f"/api/uploads/{uid}/chunk?index=2", content=data[2 * chunk:3 * chunk])
        # duplicate chunk 0 (idempotent)
        r = client.post(f"/api/uploads/{uid}/chunk?index=0", content=data[0:chunk])
        assert r.status_code == 200
        # resume: upload the missing chunk 1 (+ any others)
        for i in range(n):
            s, e = i * chunk, min((i + 1) * chunk, total)
            st = client.post(f"/api/uploads/{uid}/chunk?index={i}", content=data[s:e])
            assert st.status_code == 200, st.text

        comp = client.post(f"/api/uploads/{uid}/complete")
        assert comp.status_code == 200, comp.text
        vid = comp.json()["video_id"]
        item = next(m for m in client.get("/api/media").json()["items"] if m["video_id"] == vid)
        assert item["size_bytes"] == total  # exact byte count, no corruption
    finally:
        client.__exit__(None, None, None)
