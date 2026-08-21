"""Job orchestration: creation, querying, progress updates, cancellation.

All status changes go through the explicit job state machine
(``JobRepository.transition``) so invalid transitions are rejected instead of
silently corrupting job history.
"""
from __future__ import annotations

import uuid
from typing import Optional

from ..domain.models import Job, JobStatus, JobType
from ..exceptions import NotFoundError
from ..infrastructure import metrics
from ..infrastructure.repositories import JobRepository, VideoRepository
from ..logging_config import get_logger
from ..utils import now_iso

log = get_logger(__name__)


class JobService:
    def __init__(self, job_repo: JobRepository, video_repo: VideoRepository, gate=None):
        self.jobs = job_repo
        self.videos = video_repo
        self.gate = gate

    def create_index_job(
        self, video_id: str, type: str = JobType.INDEX.value, media_type: str = "video"
    ) -> str:
        if self.gate is not None:
            self.gate.require_not_active("indexing")
        job_id = uuid.uuid4().hex
        now = now_iso()
        job = Job(
            job_id=job_id, video_id=video_id, type=type,
            media_type=media_type, created_at=now, updated_at=now,
        )
        self.jobs.insert(job)
        metrics.inc("jobs.created")
        return job_id

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFoundError(f"job '{job_id}' not found")
        return job

    def list(self, limit: int = 50, status: Optional[str] = None) -> list[Job]:
        return self.jobs.list(limit=limit, status=status)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            return job
        if job.status == JobStatus.QUEUED.value:
            self.jobs.transition(job_id, JobStatus.QUEUED.value, JobStatus.CANCELLED.value)
            self.videos.update(job.video_id, status="cancelled")
            log.info("Job %s cancelled while queued", job_id)
        elif job.status == JobStatus.RUNNING.value:
            self.jobs.transition(job_id, JobStatus.RUNNING.value, JobStatus.CANCELLING.value)
            log.info("Cancellation requested for running job %s", job_id)
        # CANCELLING already: leave as-is
        return self.get(job_id)

    def update_progress(
        self, job_id: str, stage: str, progress: float,
        frames_processed: int | None = None, frames_total: int | None = None,
    ) -> None:
        self.jobs.set_progress(job_id, stage, progress, frames_processed, frames_total)
