"""Image-upload support: pipeline, validation, and unified image+video search."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image


def _upload(client, path: Path, chunk_size: int | None = None):
    data = path.read_bytes()
    if chunk_size is None:
        chunk_size = max(64 * 1024, (len(data) + 3) // 4)
    init = client.post("/api/uploads/init", json={
        "filename": path.name, "file_size": len(data), "chunk_size": chunk_size,
    })
    assert init.status_code == 200, init.text
    uid = init.json()["upload_id"]
    for i in range(init.json()["total_chunks"]):
        s, e = i * chunk_size, min((i + 1) * chunk_size, len(data))
        r = client.post(f"/api/uploads/{uid}/chunk?index={i}", content=data[s:e])
        assert r.status_code == 200, r.text
    comp = client.post(f"/api/uploads/{uid}/complete")
    assert comp.status_code == 200, comp.text
    return comp.json()


def _poll_job(client, job_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(0.3)
    raise TimeoutError(f"job {job_id} did not finish")


def _make_image(path: Path, size=(128, 128), color=(220, 40, 40), fmt="JPEG"):
    Image.new("RGB", size, color).save(path, fmt)
    return path


def _make_client(settings):
    from app.main import create_app

    app = create_app(settings)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.__enter__()
    return client, app


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ext,fmt", [("jpg", "JPEG"), ("png", "PNG"), ("webp", "WEBP")])
def test_image_upload_pipeline(settings, tmp_path, ext, fmt):
    client, app = _make_client(settings)
    try:
        img = _make_image(tmp_path / f"sample.{ext}", fmt=fmt)
        completed = _upload(client, img)

        job = _poll_job(client, completed["job_id"])
        assert job["status"] == "completed", job

        media = client.get("/api/media").json()
        item = next(m for m in media["items"] if m["video_id"] == completed["video_id"])
        assert item["media_type"] == "image"
        assert item["status"] == "ready"
        assert item["frame_count"] == 1
        assert item["duration_hms"] is None

        # thumbnail serves
        t = client.get(f"/api/media/{completed['video_id']}/thumbnail")
        assert t.status_code == 200

        # the image frame is servable (image IS the frame)
        frame_url = f"/api/media/{completed['video_id']}/frames/{completed['video_id']}_000000"
        f = client.get(frame_url)
        assert f.status_code == 200
        assert f.headers["content-type"].startswith("image/")

        # metadata search returns the image with media_type=image
        res = client.post("/api/search", json={"query": "sample", "mode": "metadata"})
        ids = {r["video_id"] for r in res.json()["results"]}
        assert completed["video_id"] in ids
    finally:
        client.__exit__(None, None, None)


def test_image_upload_rejects_corrupt_file(settings, tmp_path):
    client, app = _make_client(settings)
    try:
        bad = tmp_path / "fake.jpg"
        bad.write_bytes(b"this is not a real image" * 50)
        data = bad.read_bytes()
        init = client.post("/api/uploads/init", json={
            "filename": "fake.jpg", "file_size": len(data), "chunk_size": len(data),
        }).json()
        r = client.post(f"/api/uploads/{init['upload_id']}/chunk?index=0", content=data)
        assert r.status_code == 200
        comp = client.post(f"/api/uploads/{init['upload_id']}/complete")
        assert comp.status_code == 400, comp.text
        assert "image" in comp.json()["error"]["message"].lower()
    finally:
        client.__exit__(None, None, None)


def test_image_upload_rejects_oversized_pixels(settings, tmp_path):
    client, app = _make_client(settings)
    try:
        # lower the limit to a tiny value to prove the guard fires
        app.state.container.settings.max_image_pixels = 100
        img = _make_image(tmp_path / "big.jpg", size=(200, 200))
        data = img.read_bytes()
        init = client.post("/api/uploads/init", json={
            "filename": "big.jpg", "file_size": len(data), "chunk_size": len(data),
        }).json()
        client.post(f"/api/uploads/{init['upload_id']}/chunk?index=0", content=data)
        comp = client.post(f"/api/uploads/{init['upload_id']}/complete")
        assert comp.status_code == 400
        assert "pixels" in comp.json()["error"]["message"].lower()
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# unified image + video semantic search (requires SigLIP)
# ---------------------------------------------------------------------------
def _make_red_video(path: Path, seconds: int = 4) -> Path:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"color=c=0xcc2222:s=160x120:r=10:d={seconds}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip("ffmpeg unavailable")
    return path


@pytest.mark.ml
def test_image_and_video_unified_semantic_search(settings, tmp_path):
    if settings.embedding_backend != "siglip":
        pytest.skip("requires SigLIP")
    client, app = _make_client(settings)
    try:
        container = app.state.container
        if container.embedding.name != "siglip":
            pytest.skip("SigLIP unavailable")

        # upload a red image and a red video
        img = _make_image(tmp_path / "red_object.jpg", color=(210, 40, 40))
        vid = _make_red_video(tmp_path / "red.mp4")
        img_comp = _upload(client, img)
        vid_comp = _upload(client, vid)
        _poll_job(client, img_comp["job_id"])
        _poll_job(client, vid_comp["job_id"])

        res = client.post("/api/search", json={
            "query": "a red object", "mode": "fast", "final_results": 10,
        }).json()
        types = {r["media_type"] for r in res["results"]}
        # both an image and a video frame are retrieved in the same space
        assert "image" in types, f"no image result: {res['results']}"
        assert "video" in types, f"no video result: {res['results']}"

        # media_type filter: images only
        res_img = client.post("/api/search", json={
            "query": "red", "mode": "fast", "final_results": 10, "media_type": "image",
        }).json()
        assert res_img["results"]
        assert all(r["media_type"] == "image" for r in res_img["results"])
    finally:
        client.__exit__(None, None, None)
