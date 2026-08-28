"""Per-video coordination + global maintenance + chunk/upload limiting.

* ``VideoCoordinator`` — per-video locks + cancel-and-wait protocol so INDEX /
  REINDEX / DELETE of the same video never overlap.
* ``MaintenanceGate`` — a global barrier used by destructive clear-all
  operations. While active: new indexing jobs are rejected, the worker stops
  picking up work, and in-flight operations observe the flag.
* ``ChunkLocks`` — per-(upload_id, chunk_index) locks so a chunk is written by
  exactly one request at a time.
* ``UploadLimiter`` — a bounded semaphore enforcing MAX_CONCURRENT_UPLOADS
  within the process (the DB count remains the reporting/authoritative gauge).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from ..logging_config import get_logger

log = get_logger(__name__)


class VideoCoordinator:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, video_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(video_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[video_id] = lock
            return lock

    @contextmanager
    def hold(self, video_id: str) -> Iterator[None]:
        lock = self._lock_for(video_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def wait_until_no_active_jobs(
        self,
        video_id: str,
        active_lookup: Callable[[str], list],
        cancel: Callable[[str], None],
        timeout: float = 30.0,
        poll: float = 0.2,
    ) -> list[str]:
        """Request cancellation of every active job for a video and wait for
        them to reach a terminal state."""
        deadline = time.time() + timeout
        while True:
            active = active_lookup(video_id)
            if not active:
                return []
            for job in active:
                if job.status in ("queued", "running", "cancelling"):
                    try:
                        cancel(job.job_id)
                    except Exception as exc:  # pragma: no cover - defensive
                        log.warning("cancel(%s) failed: %s", job.job_id, exc)
            if time.time() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for active jobs on video {video_id} to terminate"
                )
            time.sleep(poll)


class MaintenanceGate:
    """Global maintenance barrier (single-writer, visible to all operations)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> None:
        with self._lock:
            was = self._active
            self._active = True
        if not was:
            log.warning("GLOBAL MAINTENANCE entered")

    def stop(self) -> None:
        with self._lock:
            was = self._active
            self._active = False
        if was:
            log.warning("GLOBAL MAINTENANCE exited")

    def require_not_active(self, operation: str = "operation") -> None:
        """Raise if maintenance is active (used to reject new indexing)."""
        from ..exceptions import ConflictError

        if self.active:
            raise ConflictError(
                f"system maintenance in progress; {operation} is temporarily unavailable"
            )

    @contextmanager
    def enter(self) -> Iterator[None]:
        """Enter maintenance for the duration of a context (idempotent)."""
        self.start()
        try:
            yield
        finally:
            self.stop()


class ChunkLocks:
    """Per-(upload_id, chunk_index) locks."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, int], threading.Lock] = {}

    @contextmanager
    def hold(self, upload_id: str, index: int) -> Iterator[None]:
        key = (upload_id, index)
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def forget(self, upload_id: str) -> None:
        with self._guard:
            self._locks = {k: v for k, v in self._locks.items() if k[0] != upload_id}


class UploadLimiter:
    """Race-safe concurrent-upload limit (process-local semaphore)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self._semaphore = threading.BoundedSemaphore(self.limit) if self.limit > 0 else None
        self._guard = threading.Lock()
        self._held: set[str] = set()

    def acquire(self, upload_id: str) -> None:
        from ..exceptions import ConflictError

        if self._semaphore is not None and not self._semaphore.acquire(blocking=False):
            raise ConflictError("too many concurrent uploads; try again later")
        with self._guard:
            self._held.add(upload_id)

    def release(self, upload_id: str) -> None:
        with self._guard:
            was_held = upload_id in self._held
            if was_held:
                self._held.discard(upload_id)
        if was_held and self._semaphore is not None:
            self._semaphore.release()

    @property
    def active_count(self) -> int:
        with self._guard:
            return len(self._held)


class KeyedLocks:
    """Generic keyed lock registry (fine-cache windows, etc.).

    Callers use ``hold(key)`` to serialise work per key. The second request
    must re-check its condition AFTER acquiring the lock (see
    ``SearchService._fine_search_window``) so two concurrent requests never
    both extract the same window.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = {}

    @contextmanager
    def hold(self, key: tuple) -> Iterator[None]:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
