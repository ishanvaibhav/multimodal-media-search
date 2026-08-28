"""Admin / destructive operations (authenticated) + global maintenance.

DELETE ALL runs the global maintenance barrier with FULLY ASYNC quiescence:

    ENTER maintenance
        → stop accepting new indexing jobs / uploads / fine-cache writes
        → cancel queued/running jobs
        → AWAIT worker quiescence (async, never blocks the event loop)
        → AWAIT fine-search cache-write quiescence
        → delete Chroma + media/frame/cache data + DB records
        → validate clean state
    EXIT maintenance (only on success)

If workers cannot quiesce in time, DELETE ALL fails WITHOUT reporting success
and the system REMAINS in maintenance so the destructive operation can be
retried safely.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ..exceptions import ValidationError
from ..logging_config import get_logger
from ..schemas.admin import AdminClearRequest, AdminClearResponse, MaintenanceState
from .deps import get_container, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

log = get_logger(__name__)

_REQUIRED_CONFIRMATION = "DELETE ALL"


@router.delete("/data", response_model=AdminClearResponse)
async def clear_all_data(body: AdminClearRequest, request: Request):
    if body.confirmation != _REQUIRED_CONFIRMATION:
        raise ValidationError(
            f"confirmation must be exactly '{_REQUIRED_CONFIRMATION}'"
        )
    container = get_container(request)
    timeout = container.settings.job_cancel_timeout_seconds

    # ENTER maintenance: new jobs, uploads and fine-cache writes are rejected
    container.gate.start()
    success = False
    try:
        # 1) cancel every queued/running job
        container.job_repo.cancel_all_active()
        for job in container.job_repo.list_active():
            container.worker.request_cancel(job.job_id)

        # 2) AWAIT worker quiescence (async — the event loop stays responsive,
        #    so cancellation actually propagates to the running job threads)
        idle = await container.worker.wait_until_idle(timeout)
        if not idle or container.job_repo.list_active():
            raise ValidationError(
                "active workers did not stop within the cancel timeout; "
                "DELETE ALL aborted and maintenance retained"
            )

        # 3) AWAIT fine-search cache-write quiescence (async polling)
        deadline = asyncio.get_running_loop().time() + timeout
        while container.search_service.fine_active > 0:
            if asyncio.get_running_loop().time() >= deadline:
                raise ValidationError(
                    "fine-search extractions did not quiesce within the timeout; "
                    "DELETE ALL aborted and maintenance retained"
                )
            await asyncio.sleep(0.05)

        # 4) wipe everything (no background operation can mutate any more).
        #    Heavy filesystem/Chroma work runs off the event loop.
        def _wipe_and_validate():
            container.vectorstore.delete_all()
            deleted_files = container.storage.delete_all_media_artifacts()
            temp_cleared = container.storage.clear_temp()

            db = container.database
            tables = (
                "frames", "videos", "uploads", "upload_chunks", "jobs",
                "search_history", "feedback", "fine_cache_intervals",
                "saved_contexts",
            )
            deleted_rows = {}
            for table in tables:
                deleted_rows[table] = db.execute(f"DELETE FROM {table}")

            post = container.consistency_service.check(repair=False)
            return deleted_files, temp_cleared, deleted_rows, post

        deleted_files, temp_cleared, deleted_rows, post = await asyncio.to_thread(
            _wipe_and_validate
        )

        # 5) validate clean state BEFORE releasing maintenance
        if (
            post["videos"] or post["frames"] or post["vectors"] > 0
            or post["orphan_files"] or post["orphan_jobs"]
        ):
            raise ValidationError(
                f"post-delete validation found residual state: {post}"
            )

        success = True
    finally:
        if success:
            container.gate.stop()
        else:
            # remain in maintenance so the destructive op can be retried
            log.error("DELETE ALL incomplete; system remains in maintenance")

    log.warning("DELETE ALL completed; system reset")
    return AdminClearResponse(
        cleared=True,
        deleted={"rows": deleted_rows, "files": deleted_files, "temp_files": temp_cleared},
    )


@router.post("/maintenance/start", response_model=MaintenanceState)
async def maintenance_start(request: Request):
    container = get_container(request)
    container.gate.start()
    return MaintenanceState(maintenance=True)


@router.post("/maintenance/stop", response_model=MaintenanceState)
async def maintenance_stop(request: Request):
    container = get_container(request)
    container.gate.stop()
    return MaintenanceState(maintenance=False)


@router.get("/maintenance", response_model=MaintenanceState)
async def maintenance_status(request: Request):
    container = get_container(request)
    return MaintenanceState(maintenance=container.gate.active)
