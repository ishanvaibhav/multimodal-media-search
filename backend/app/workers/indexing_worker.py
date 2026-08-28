"""Background indexing worker.

A lightweight single-process worker: it polls the jobs table for QUEUED jobs
and executes the indexing pipeline inside a thread-pool executor so the API
event loop never blocks. Concurrency is bounded by MAX_CONCURRENT_JOBS.

**Coordination**: each pipeline run holds the per-video lock for its whole
duration, so delete/reindex of the same video must first cancel the job and
wait — the two can never overlap.

Cancellation is cooperative: the worker records cancel requests, the pipeline
checks them between stages (and FFmpeg is terminated), and the job reaches a
terminal state before the lock is released.
"""
from __future__ import annotations

import asyncio
import threading
import time

from ..domain.models import JobStatus
from ..logging_config import get_logger

log = get_logger(__name__)

_POLL_INTERVAL = 1.0


class IndexingWorker:
    def __init__(self, container):
        self.container = container
        self.jobs = container.job_repo
        self.indexing = container.indexing_service
        self.coordinator = container.coordinator
        self.settings = container.settings
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self._cancel_requested: set[str] = set()
        self._last_heartbeat = time.time()
        self._running_count = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="indexing-worker")
        log.info("Indexing worker started (max_concurrent_jobs=%d)",
                 self.settings.max_concurrent_jobs)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Indexing worker stopped")

    # -- cancellation ----------------------------------------------------
    def request_cancel(self, job_id: str) -> None:
        self._cancel_requested.add(job_id)

    def _is_cancelled(self, job_id: str) -> bool:
        if job_id in self._cancel_requested:
            return True
        job = self.jobs.get(job_id)
        return job is not None and job.status in (
            JobStatus.CANCELLING.value, JobStatus.CANCELLED.value
        )

    # -- main loop --------------------------------------------------------
    async def _run(self) -> None:
        while not self._stop.is_set():
            self._last_heartbeat = time.time()
            try:
                # never pick up work during global maintenance
                if (
                    self._running_count < self.settings.max_concurrent_jobs
                    and not self.container.gate.active
                ):
                    job = self.jobs.next_queued()
                    if job is not None:
                        self._running_count += 1
                        asyncio.get_running_loop().create_task(self._process(job))
            except Exception:  # pragma: no cover
                log.exception("worker loop error")
            await asyncio.sleep(_POLL_INTERVAL)

    async def _process(self, job) -> None:
        try:
            # re-read: the job may have been cancelled before we started
            current = self.jobs.get(job.job_id)
            if current is None or current.status != JobStatus.QUEUED.value:
                return

            def cancel_check():
                return self._is_cancelled(job.job_id)

            # hold the per-video lock for the whole pipeline run
            with self.coordinator.hold(job.video_id):
                if self._is_cancelled(job.job_id):
                    return
                await asyncio.to_thread(self.indexing.run_job, job, cancel_check)
        except Exception as exc:  # pragma: no cover
            log.exception("unexpected worker failure for job %s: %s", job.job_id, exc)
        finally:
            self._cancel_requested.discard(job.job_id)
            self._running_count -= 1

    def heartbeat_ok(self, max_age_seconds: float = 30.0) -> bool:
        return (time.time() - self._last_heartbeat) < max_age_seconds

    @property
    def running_count(self) -> int:
        return self._running_count

    # -- public lifecycle/state API (no private internals from HTTP handlers) --
    def active_jobs(self) -> list:
        return self.jobs.list_active()

    def worker_state(self) -> dict:
        """Observable worker state (used by health/admin, never internals)."""
        return {
            "running": self._running_count,
            "queued": self.jobs.queued_count(),
            "maintenance": self.container.gate.active,
            "heartbeat_ok": self.heartbeat_ok(),
        }

    async def shutdown(self, timeout: float = 30.0) -> bool:
        """Request cancellation of all work, await idle, then stop the loop."""
        self.jobs.cancel_all_active()
        for job in self.jobs.list_active():
            self.request_cancel(job.job_id)
        idle = await self.wait_until_idle(timeout)
        self._stop.set()
        return idle

    # -- quiescence contract ---------------------------------------------
    async def wait_until_idle(self, timeout: float) -> bool:
        """Await true worker quiescence WITHOUT blocking the event loop.

        Idle means: no running indexing jobs (each job thread fully finished —
        including FFmpeg subprocesses and Chroma writes — before
        ``_running_count`` decrements), and the loop will not pick up new work
        because global maintenance is active.

        Returns True when idle, False on timeout.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._running_count == 0:
                return True
            await asyncio.sleep(0.05)
        return self._running_count == 0
