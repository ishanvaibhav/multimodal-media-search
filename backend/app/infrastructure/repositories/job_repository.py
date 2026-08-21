from __future__ import annotations

from typing import Optional

from ...domain.models import Job, JobStatus
from ...exceptions import ConflictError
from ...infrastructure.database import Database
from ...utils import now_iso

# Valid job-state transitions (explicit state machine).
VALID_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.QUEUED.value: {JobStatus.RUNNING.value, JobStatus.CANCELLED.value, JobStatus.FAILED.value},
    JobStatus.RUNNING.value: {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLING.value,
    },
    JobStatus.CANCELLING.value: {JobStatus.CANCELLED.value, JobStatus.FAILED.value},
    # terminal states have no outgoing transitions
    JobStatus.COMPLETED.value: set(),
    JobStatus.FAILED.value: set(),
    JobStatus.CANCELLED.value: set(),
}


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def insert(self, job: Job) -> None:
        now = job.created_at or now_iso()
        self.db.execute(
            """
            INSERT INTO jobs (job_id, video_id, type, status, progress, current_stage,
                frames_processed, frames_total, frames_sampled, frames_kept, frames_embedded,
                retry_count, checkpoint, media_type, error, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job.job_id, job.video_id, job.type, job.status, job.progress,
                job.current_stage, job.frames_processed, job.frames_total,
                job.frames_sampled, job.frames_kept, job.frames_embedded,
                job.retry_count, job.checkpoint, job.media_type, job.error,
                now, job.updated_at or now,
            ),
        )

    def get(self, job_id: str) -> Optional[Job]:
        row = self.db.query_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return Job.from_row(row) if row else None

    def list(self, limit: int = 50, status: Optional[str] = None) -> list[Job]:
        if status:
            rows = self.db.query(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [Job.from_row(r) for r in rows]

    def latest_for_video(self, video_id: str) -> Optional[Job]:
        row = self.db.query_one(
            "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
            (video_id,),
        )
        return Job.from_row(row) if row else None

    def active_for_video(self, video_id: str) -> list[Job]:
        rows = self.db.query(
            """
            SELECT * FROM jobs WHERE video_id = ?
              AND status IN ('queued','running','cancelling')
            ORDER BY created_at ASC
            """,
            (video_id,),
        )
        return [Job.from_row(r) for r in rows]

    def next_queued(self) -> Optional[Job]:
        row = self.db.query_one(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        )
        return Job.from_row(row) if row else None

    def running_count(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'"
        )
        return int(row["n"]) if row else 0

    def list_active(self) -> list[Job]:
        rows = self.db.query(
            """
            SELECT * FROM jobs WHERE status IN ('queued','running','cancelling')
            ORDER BY created_at ASC
            """
        )
        return [Job.from_row(r) for r in rows]

    def cancel_all_active(self) -> int:
        """Mark queued jobs cancelled and running jobs cancelling (best-effort)."""
        n = self.db.execute(
            "UPDATE jobs SET status='cancelled', updated_at=? WHERE status='queued'",
            (now_iso(),),
        )
        n += self.db.execute(
            "UPDATE jobs SET status='cancelling', updated_at=? WHERE status='running'",
            (now_iso(),),
        )
        return n

    def queued_count(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'"
        )
        return int(row["n"]) if row else 0

    def update(self, job_id: str, **fields) -> None:
        """Update non-status fields (progress, stage, counters, error)."""
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )

    def transition(self, job_id: str, from_status: str, to_status: str, **extra) -> None:
        """Perform a validated state transition (CAS-style).

        Rejects transitions that are not in the explicit state machine or that
        no longer match ``from_status`` (i.e. the job already moved on).
        """
        allowed = VALID_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ConflictError(
                f"invalid job transition {from_status} -> {to_status}"
            )
        fields = dict(extra)
        fields["status"] = to_status
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        rowcount = self.db.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ? AND status = ?",
            (*fields.values(), job_id, from_status),
        )
        if rowcount == 0:
            raise ConflictError(
                f"job '{job_id}' is not in state '{from_status}' (transition rejected)"
            )

    def set_progress(
        self, job_id: str, stage: str, progress: float,
        frames_processed: int | None = None, frames_total: int | None = None,
        frames_sampled: int | None = None, frames_kept: int | None = None,
        frames_embedded: int | None = None,
    ) -> None:
        fields: dict = {
            "current_stage": stage,
            "progress": round(max(0.0, min(100.0, float(progress))), 2),
        }
        if frames_processed is not None:
            fields["frames_processed"] = frames_processed
        if frames_total is not None:
            fields["frames_total"] = frames_total
        if frames_sampled is not None:
            fields["frames_sampled"] = frames_sampled
        if frames_kept is not None:
            fields["frames_kept"] = frames_kept
        if frames_embedded is not None:
            fields["frames_embedded"] = frames_embedded
        self.update(job_id, **fields)

    def delete_all(self) -> int:
        return self.db.execute("DELETE FROM jobs")

    def reset_stale(self, message: str) -> int:
        """Mark jobs left running/cancelling by a crash as failed."""
        n1 = self.db.execute(
            "UPDATE jobs SET status='failed', error=? WHERE status='running'", (message,)
        )
        n2 = self.db.execute(
            "UPDATE jobs SET status='failed', error=? WHERE status='cancelling'", (message,)
        )
        return n1 + n2

    def orphaned(self) -> list[Job]:
        """Active (non-terminal) jobs whose video no longer exists.

        Terminal jobs referencing deleted videos are retained as audit history
        and are intentionally NOT flagged.
        """
        rows = self.db.query(
            """
            SELECT j.* FROM jobs j LEFT JOIN videos v ON j.video_id = v.video_id
            WHERE v.video_id IS NULL AND j.status IN ('queued','running','cancelling')
            """
        )
        return [Job.from_row(r) for r in rows]
