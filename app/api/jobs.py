import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel

from app.config import Settings
from app.observability.alerting import send_alert
from app.observability.metrics import record_job_outcome


class Job(BaseModel):
    job_id: str
    kind: Literal["lead", "discovery"]
    status: Literal["queued", "running", "done", "failed"]
    created_at: datetime
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, kind: Literal["lead", "discovery"]) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            kind=kind,
            status="queued",
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job.job_id] = job
        return job

    def mark_running(self, job_id: str) -> None:
        self._jobs[job_id].status = "running"

    def mark_done(self, job_id: str, result: Any) -> None:
        job = self._jobs[job_id]
        job.status = "done"
        job.result = result
        job.finished_at = datetime.now(timezone.utc)
        record_job_outcome(kind=job.kind, status="done")

    def mark_failed(self, job_id: str, error: str, settings: Settings | None = None) -> None:
        job = self._jobs[job_id]
        job.status = "failed"
        job.error = error
        job.finished_at = datetime.now(timezone.utc)
        record_job_outcome(kind=job.kind, status="failed")
        if settings is not None:
            send_alert(settings, kind=job.kind, status="failed", error=error)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


@lru_cache
def get_job_store() -> JobStore:
    return JobStore()
