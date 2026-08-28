"""Integration tests: full upload -> index -> search -> delete lifecycle."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(settings):
    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def _poll_job(client, job_id, timeout=90.0, interval=0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/api/jobs/{job_id}")
        assert res.status_code == 200, res.text
        job = res.json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(interval)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


def _upload_video(client, video_path, chunk_size=None):
    data = video_path.read_bytes()
    total = len(data)
    if chunk_size is None:
        chunk_size = max(64 * 1024, (total + 3) // 4)  # force >= 4 chunks
    init = client.post("/api/uploads/init", json={
        "filename": "sample.mp4", "file_size": total,
        "content_type": "video/mp4", "chunk_size": chunk_size,
    })
    assert init.status_code == 200, init.text
    upload_id = init.json()["upload_id"]
    total_chunks = init.json()["total_chunks"]
    assert total_chunks > 1  # ensure multi-chunk path is exercised

    # upload chunk 0 and 2 first, skipping 1 -> tests resume/out-of-order
    order = [0, 2] + [i for i in range(total_chunks) if i not in (0, 2)]
    for i in order:
        start, end = i * chunk_size, min((i + 1) * chunk_size, total)
        res = client.post(
            f"/api/uploads/{upload_id}/chunk?index={i}",
            content=data[start:end],
            headers={"Content-Type": "application/octet-stream"},
        )
        assert res.status_code == 200, res.text

    status = client.get(f"/api/uploads/{upload_id}/status").json()
    assert status["received_chunks"] == total_chunks
    assert status["received_bytes"] == total

    complete = client.post(f"/api/uploads/{upload_id}/complete")
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["status"] == "completed"
    return body


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["api"] == "ok"
    assert data["database"] == "ok"
    assert data["chromadb"] == "ok"


def test_full_lifecycle(client, sample_video):
    # 1. upload
    completed = _upload_video(client, sample_video)
    video_id, job_id = completed["video_id"], completed["job_id"]

    # 2. job runs to completion
    job = _poll_job(client, job_id)
    assert job["status"] == "completed", job

    # 3. media library reflects the indexed video
    media = client.get("/api/media").json()
    assert media["total"] == 1
    item = media["items"][0]
    assert item["video_id"] == video_id
    assert item["status"] == "ready"
    assert item["frame_count"] > 0
    assert item["duration_seconds"] is not None

    # 4. video detail contains frames
    detail = client.get(f"/api/media/{video_id}").json()
    assert len(detail["frames"]) > 0

    # 5. frame serving
    frame_id = detail["frames"][0]["frame_id"]
    fr = client.get(f"/api/media/{video_id}/frames/{frame_id}")
    assert fr.status_code == 200
    assert fr.headers["content-type"].startswith("image/")

    # 6. video streaming with Range requests
    stream = client.get(f"/api/media/{video_id}/stream", headers={"Range": "bytes=0-1023"})
    assert stream.status_code == 206
    assert stream.headers.get("content-range", "").startswith("bytes 0-1023/")

    # 7. search returns a structured response
    search_res = client.post("/api/search", json={"query": "a colorful pattern"})
    assert search_res.status_code == 200
    search_body = search_res.json()
    assert "results" in search_body
    assert search_body["total_candidates"] >= 0

    # 8. search history recorded
    history = client.get("/api/search/history").json()
    assert len(history["items"]) >= 1

    # 9. delete the video -> metadata + vectors + files removed
    deleted = client.delete(f"/api/media/{video_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"]["video_file"] == 1

    assert client.get("/api/media").json()["total"] == 0
    assert client.get(f"/api/media/{video_id}").status_code == 404


def test_date_filter_backend(client, sample_video):
    completed = _upload_video(client, sample_video)
    job = _poll_job(client, completed["job_id"])
    assert job["status"] == "completed"

    # a date range far in the past should exclude the freshly uploaded video
    res = client.post("/api/search", json={
        "query": "colorful",
        "date_from": "2020-01-01",
        "date_to": "2020-12-31",
    })
    assert res.status_code == 200
    assert res.json()["total_candidates"] == 0


def test_admin_clear_requires_confirmation(client, sample_video):
    completed = _upload_video(client, sample_video)
    _poll_job(client, completed["job_id"])

    bad = client.request("DELETE", "/api/admin/data", json={"confirmation": "yes"})
    assert bad.status_code == 400

    good = client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
    assert good.status_code == 200
    assert good.json()["cleared"] is True
    assert client.get("/api/media").json()["total"] == 0


def test_upload_abort(client, sample_video):
    data = sample_video.read_bytes()
    init = client.post("/api/uploads/init", json={
        "filename": "sample.mp4", "file_size": len(data), "chunk_size": 200_000,
    }).json()
    uid = init["upload_id"]
    client.post(f"/api/uploads/{uid}/chunk?index=0", content=data[:200_000])
    res = client.delete(f"/api/uploads/{uid}")
    assert res.status_code == 200
    assert client.get(f"/api/uploads/{uid}/status").status_code == 404


def test_jobs_listing(client, sample_video):
    completed = _upload_video(client, sample_video)
    _poll_job(client, completed["job_id"])
    jobs = client.get("/api/jobs").json()
    assert len(jobs) >= 1
    assert any(j["status"] == "completed" for j in jobs)
