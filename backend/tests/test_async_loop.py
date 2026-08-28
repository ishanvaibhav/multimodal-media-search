"""Async-event-loop regression tests.

These specifically catch a future regression where blocking primitives
(``time.sleep``, ``threading.Event.wait``, blocking subprocess waits) are
reintroduced into async request paths: the heartbeat coroutine would stall.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.domain.models import Job, Video
from app.utils import now_iso


def _make_tiny_video(path: Path) -> Path:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "color=c=0x22aa22:s=160x120:r=10:d=6", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-y", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        pytest.skip("ffmpeg unavailable")
    return path


@pytest.mark.asyncio
async def test_delete_all_does_not_block_event_loop(settings, tmp_path):
    from app.main import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        container = app.state.container

        # seed a video + queued job whose embedding stage is deliberately slow
        v = _make_tiny_video(tmp_path / "slow.mp4")
        dest = container.settings.media_dir / "slow.mp4"
        shutil.copyfile(v, dest)
        container.video_repo.insert(Video(
            video_id="slow", filename="slow.mp4", original_filename="slow.mp4",
            path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
            status="queued", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
        ))
        container.job_repo.insert(Job(job_id="js", video_id="slow"))

        # slow down embedding so the job is still running when DELETE ALL starts
        dim = container.embedding.dim
        orig_embed = container.embedding.embed_images

        def slow_embed(images):
            time.sleep(2.0)
            return np.zeros((len(images), dim), dtype="float32")

        container.embedding.embed_images = slow_embed

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # wait until the worker actually starts the job
            for _ in range(50):
                if container.worker.running_count > 0:
                    break
                await asyncio.sleep(0.1)

            # run DELETE ALL as a task; concurrently run a heartbeat coroutine
            delete_task = asyncio.create_task(
                client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
            )
            ticks = [asyncio.get_running_loop().time()]
            while not delete_task.done():
                ticks.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.05)
                if len(ticks) > 400:  # hard cap ~20s
                    delete_task.cancel()
                    break

            resp = await delete_task
            assert resp.status_code == 200, resp.text

            # the heartbeat must have kept ticking: no long stalls
            gaps = [b - a for a, b in zip(ticks, ticks[1:])]
            assert max(gaps) < 1.0, f"event loop stalled: max gap {max(gaps):.2f}s"

            # nothing was recreated after the wipe
            media_after = await client.get("/api/media")
            health_after = await client.get("/api/health")
            assert media_after.json()["total"] == 0
            assert health_after.json()["details"]["vectors"] == 0

        container.embedding.embed_images = orig_embed


@pytest.mark.asyncio
async def test_worker_wait_until_idle_timeout(container):
    # simulate a stuck running job
    container.worker._running_count = 1
    started = asyncio.get_running_loop().time()
    idle = await container.worker.wait_until_idle(0.3)
    elapsed = asyncio.get_running_loop().time() - started
    assert idle is False
    assert elapsed < 1.0  # returned promptly (no long block)
    container.worker._running_count = 0
    assert await container.worker.wait_until_idle(0.3) is True


@pytest.mark.asyncio
async def test_delete_all_keeps_maintenance_on_failure(settings):
    from app.main import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        container = app.state.container
        # simulate an unkillable worker: a stuck running job with a tiny timeout
        container.settings.job_cancel_timeout_seconds = 0.2
        container.worker._running_count = 1
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(
                "DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"}
            )
        # request failed and the system REMAINS in maintenance
        assert resp.status_code != 200
        assert container.gate.active is True
        container.worker._running_count = 0
        container.gate.stop()
