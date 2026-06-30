from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4


@dataclass(slots=True)
class JobRecord:
    job_id: UUID
    status: str
    result: dict[str, Any] | None = None


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[UUID, JobRecord] = {}

    def create(self) -> JobRecord:
        job = JobRecord(job_id=uuid4(), status="queued")
        self._jobs[job.job_id] = job
        return job

    def save_result(self, job_id: UUID, result: dict[str, Any]) -> JobRecord:
        job = self._jobs[job_id]
        job.status = "completed"
        job.result = result
        return job

    def get(self, job_id: UUID) -> JobRecord | None:
        return self._jobs.get(job_id)
