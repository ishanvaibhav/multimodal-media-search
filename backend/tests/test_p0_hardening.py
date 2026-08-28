"""P0 regression tests: global delete-all safety, maintenance barrier,
upload completion idempotency, chunk concurrency, upload limit."""
from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_video(path: Path, seconds: int = 4, hue: str = "red") -> Path:
    color = {"red": "0xff3333", "blue": "0x3333ff"}[hue]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=10:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {proc.stderr[-200:]}")
    return path


def _upload_via_api(client, path: Path, chunk_size: int | None = None):
    data = path.read_bytes()
    if chunk_size is None:
        chunk_size = max(64 * 1024, (len(data) + 3) // 4)
    init = client.post("/api/uploads/init", json={
        "filename": path.name, "file_size": len(data), "chunk_size": chunk_size,
    }).json()
    uid = init["upload_id"]
    for i in range(init["total_chunks"]):
        s, e = i * chunk_size, min((i + 1) * chunk_size, len(data))
        r = client.post(f"/api/uploads/{uid}/chunk?index={i}", content=data[s:e])
        assert r.status_code == 200, r.text
    comp = client.post(f"/api/uploads/{uid}/complete")
    assert comp.status_code == 200, comp.text
    return comp.json()


# ---------------------------------------------------------------------------
# Global delete-all safety
# ---------------------------------------------------------------------------
def test_new_index_job_rejected_during_maintenance(container):
    from app.exceptions import ConflictError

    container.gate.start()
    try:
        with pytest.raises(ConflictError):
            container.job_service.create_index_job("some_video")
    finally:
        container.gate.stop()
    # after maintenance ends, job creation works again
    job_id = container.job_service.create_index_job("some_video")
    assert job_id


def test_upload_rejected_during_maintenance(container):
    from app.exceptions import ConflictError

    container.gate.start()
    try:
        with pytest.raises(ConflictError):
            container.upload_service.init("x.mp4", 1024)
    finally:
        container.gate.stop()


def test_delete_all_idempotent_and_clean(settings, tmp_path):
    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as client:
        v = _make_video(tmp_path / "v.mp4")
        completed = _upload_via_api(client, v)
        # wait for indexing to finish
        import time
        for _ in range(100):
            j = client.get(f"/api/jobs/{completed['job_id']}").json()
            if j["status"] in ("completed", "failed"):
                break
            time.sleep(0.3)
        assert client.get("/api/media").json()["total"] == 1

        # first delete-all
        r1 = client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
        assert r1.status_code == 200, r1.text
        assert client.get("/api/media").json()["total"] == 0

        # repeat delete-all on an empty system succeeds
        r2 = client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
        assert r2.status_code == 200, r2.text

        # and a new upload + index still works afterwards
        completed2 = _upload_via_api(client, v)
        assert client.get("/api/media").json()["total"] == 1


def test_delete_all_during_running_job_no_resurrection(settings, tmp_path):
    import time

    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as client:
        v = _make_video(tmp_path / "long.mp4", seconds=6, hue="blue")
        completed = _upload_via_api(client, v)
        # delete immediately while the worker is (likely) indexing
        r = client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
        assert r.status_code == 200, r.text
        # give any in-flight worker time to fail/cleanup
        time.sleep(2.0)
        assert client.get("/api/media").json()["total"] == 0
        assert client.get("/api/health").json()["details"]["vectors"] == 0
        # nothing gets resurrected
        time.sleep(1.0)
        assert client.get("/api/media").json()["total"] == 0
        assert client.get("/api/health").json()["details"]["vectors"] == 0


# ---------------------------------------------------------------------------
# Upload completion idempotency
# ---------------------------------------------------------------------------
def test_concurrent_complete_creates_single_video(container, sample_video):
    data = sample_video.read_bytes()
    chunk = max(64 * 1024, (len(data) + 3) // 4)
    up = container.upload_service.init("v.mp4", len(data), chunk_size=chunk)

    def run(i):
        s, e = i * chunk, min((i + 1) * chunk, len(data))
        asyncio.run(_chunk(container, up.upload_id, i, data[s:e]))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(up.total_chunks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    results = []
    lock = threading.Lock()

    def complete():
        try:
            res = container.upload_service.complete(up.upload_id)
        except Exception as exc:  # noqa: BLE001
            res = {"error": str(exc)}
        with lock:
            results.append(res)

    ct = [threading.Thread(target=complete) for _ in range(12)]
    for t in ct:
        t.start()
    for t in ct:
        t.join()

    completed = [r for r in results if r.get("status") == "completed"]
    assert completed, f"no completion succeeded: {results}"
    video_ids = {r["video_id"] for r in completed}
    assert len(video_ids) == 1

    # exactly one video record and one job
    assert container.video_repo.count() == 1
    jobs = container.job_repo.list(limit=100)
    assert len(jobs) == 1
    # upload reached completed with the result stored
    upload_row = container.upload_repo.get(up.upload_id)
    assert upload_row.status == "completed"
    assert upload_row.result_video_id == next(iter(video_ids))


async def _chunk(container, upload_id, index, payload):
    async def body():
        yield payload

    await container.upload_service.receive_chunk(upload_id, index, body())


def test_repeat_complete_after_success_returns_same_result(container, sample_video):
    data = sample_video.read_bytes()
    chunk = max(64 * 1024, (len(data) + 3) // 4)
    up = container.upload_service.init("v.mp4", len(data), chunk_size=chunk)
    for i in range(up.total_chunks):
        s, e = i * chunk, min((i + 1) * chunk, len(data))
        asyncio.run(_chunk(container, up.upload_id, i, data[s:e]))
    first = container.upload_service.complete(up.upload_id)
    second = container.upload_service.complete(up.upload_id)
    assert first["video_id"] == second["video_id"]
    assert first["job_id"] == second["job_id"]
    assert container.video_repo.count() == 1
    assert len(container.job_repo.list(limit=100)) == 1


# ---------------------------------------------------------------------------
# Chunk concurrency
# ---------------------------------------------------------------------------
def test_concurrent_identical_chunk_uploads(container):
    payload = b"A" * 128
    up = container.upload_service.init("v.mp4", 256, chunk_size=128)

    def go():
        asyncio.run(_chunk(container, up.upload_id, 0, payload))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chunk_row = container.upload_repo.get_chunk(up.upload_id, 0)
    assert chunk_row is not None
    assert chunk_row["size_bytes"] == 128


def test_conflicting_chunk_content_rejected(container):
    from app.exceptions import ConflictError

    up = container.upload_service.init("v.mp4", 256, chunk_size=128)
    asyncio.run(_chunk(container, up.upload_id, 0, b"A" * 128))
    with pytest.raises(ConflictError):
        asyncio.run(_chunk(container, up.upload_id, 0, b"B" * 128))


def test_chunk_retry_after_success_is_idempotent(container):
    up = container.upload_service.init("v.mp4", 128, chunk_size=128)
    asyncio.run(_chunk(container, up.upload_id, 0, b"C" * 128))
    # retry identical bytes -> success, no exception
    asyncio.run(_chunk(container, up.upload_id, 0, b"C" * 128))
    st = container.upload_service.status(up.upload_id)
    assert st["received_chunks"] == 1
    assert st["received_bytes"] == 128


# ---------------------------------------------------------------------------
# Upload concurrency limit
# ---------------------------------------------------------------------------
def test_upload_limit_is_race_safe(tmp_path):
    from app.config import Settings
    from app.container import build_container
    from app.exceptions import ConflictError

    data_dir = tmp_path / "data"
    s = Settings(
        data_dir=str(data_dir), chroma_path=str(data_dir / "chroma"),
        embedding_backend="deterministic", max_concurrent_uploads=2, _env_file=None,
    )
    c = build_container(s)
    u1 = c.upload_service.init("a.mp4", 1024)
    u2 = c.upload_service.init("b.mp4", 1024)
    with pytest.raises(ConflictError):
        c.upload_service.init("c.mp4", 1024)
    # release one slot, then a new upload succeeds
    c.upload_service.abort(u1.upload_id)
    c.upload_service.init("d.mp4", 1024)
    assert c.limiter.active_count <= 2
