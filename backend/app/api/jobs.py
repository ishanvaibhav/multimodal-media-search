"""Job status and control endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..schemas.common import JobOut
from ..utils import validate_id
from .deps import get_container

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    status: str | None = None,
):
    jobs = get_container(request).job_service.list(limit=limit, status=status)
    return [JobOut(**j.to_dict()) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, request: Request):
    validate_id(job_id, "job_id")
    job = get_container(request).job_service.get(job_id)
    return JobOut(**job.to_dict())


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, request: Request):
    validate_id(job_id, "job_id")
    container = get_container(request)
    job = container.job_service.cancel(job_id)
    container.worker.request_cancel(job_id)
    return JobOut(**job.to_dict())
