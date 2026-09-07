import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.database.job_repository import JobRepository
from app.database.models import Database


@dataclass(slots=True)
class JobRecord:
    job_id: UUID
    status: str
    result: dict[str, Any] | None = None


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[UUID, JobRecord] = {}

    def create(self, filename: str = "unknown", content_type: str | None = None, size_bytes: int | None = None) -> JobRecord:
        job = JobRecord(job_id=uuid4(), status="queued")
        self._jobs[job.job_id] = job
        return job

    def mark_processing(self, job_id: UUID) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].status = "processing"

    def save_result(self, job_id: UUID, result: dict[str, Any]) -> JobRecord:
        job = self._jobs[job_id]
        job.status = "failed" if result.get("error") and not result.get("documents") else "completed"
        job.result = result
        return job

    def get(self, job_id: UUID) -> JobRecord | None:
        return self._jobs.get(job_id)

    def fail_job(self, job_id: UUID, error: str) -> None:
        """Mark a job failed (e.g. cancelled background task)."""
        if job_id in self._jobs:
            self._jobs[job_id].status = "failed"
            self._jobs[job_id].result = {"documents": [], "error": error}

    def get_error(self, job_id: UUID) -> str | None:
        """Return the stored error message for a failed job, if any."""
        job = self._jobs.get(job_id)
        if job and job.result:
            error = job.result.get("error")
            return str(error) if error else None
        return None

    def fail_stale_queued(self) -> int:
        """In-memory store dies with the process — nothing can be stale."""
        return 0


class SQLiteJobStore:
    """Persistent job store using SQLite database."""

    def __init__(self, db_path: str | None = None):
        self.db = Database(db_path)
        self.repo = JobRepository(self.db)

    def create(self, filename: str = "unknown", content_type: str | None = None, size_bytes: int | None = None) -> JobRecord:
        """Create a new job in the database."""
        job_id = uuid4()
        self.repo.create_job(job_id, filename, content_type, size_bytes)
        return JobRecord(job_id=job_id, status="queued")

    def mark_processing(self, job_id: UUID) -> None:
        self.repo.update_job_status(job_id, "processing")

    def save_result(self, job_id: UUID, result: dict[str, Any]) -> JobRecord:
        """Save extraction result to the database."""
        # Extract data from result
        documents = result.get("documents", [])
        if documents:
            doc = documents[0]  # Get first document result
            self.repo.complete_job(
                job_id=job_id,
                doc_type=doc.get("doc_type"),
                language=doc.get("language"),
                extraction_source=doc.get("extraction_source"),
                completeness_score=doc.get("completeness_score", 1.0),
                needs_review=doc.get("needs_review", False),
                validation_errors=doc.get("validation_errors", []),
                fields=doc.get("fields", []),
                judge_result=doc.get("judge"),
            )
        else:
            # No documents, mark as failed
            error = result.get("error", "No documents extracted")
            self.repo.update_job_status(job_id, "failed", error)

        return JobRecord(job_id=job_id, status="completed", result=result)

    def get(self, job_id: UUID) -> JobRecord | None:
        """Get a job from the database."""
        job = self.repo.get_job(job_id)
        if not job:
            return None

        # Columns stored as JSON strings must be deserialized back, or the
        # FileExtractionResponse validation fails and polls see no result.
        validation_errors = job.get("validation_errors") or []
        if isinstance(validation_errors, str):
            try:
                validation_errors = json.loads(validation_errors)
            except ValueError:
                validation_errors = []
        judge = job.get("judge")
        if isinstance(judge, dict) and isinstance(judge.get("issues"), str):
            try:
                judge = {**judge, "issues": json.loads(judge["issues"])}
            except ValueError:
                judge = {**judge, "issues": []}

        # Reconstruct result from database
        result = {
            "request": {
                "filename": job.get("filename") or "unknown",
                "content_type": job.get("content_type"),
                "size_bytes": job.get("size_bytes"),
            },
            "documents": [{
                "id": job_id,
                "doc_type": job.get("doc_type"),
                "language": job.get("language"),
                "extraction_source": job.get("extraction_source"),
                "completeness_score": job.get("completeness_score", 1.0),
                "needs_review": bool(job.get("needs_review", False)),
                "validation_errors": validation_errors,
                "fields": job.get("fields", []),
                "judge": judge,
                "error": job.get("error"),
                "failed_stage": job.get("failed_stage"),
            }]
        }

        return JobRecord(
            job_id=job_id,
            status=job.get("status", "unknown"),
            result=result if job.get("status") == "completed" else None,
        )

    def list_jobs(self, status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """List jobs with pagination."""
        return self.repo.list_jobs(status=status, limit=limit, offset=offset)

    def get_stats(self) -> dict:
        """Get extraction statistics."""
        return self.repo.get_stats()

    def fail_job(self, job_id: UUID, error: str) -> None:
        """Mark a job failed (e.g. cancelled background task)."""
        self.repo.update_job_status(job_id, "failed", error)

    def get_error(self, job_id: UUID) -> str | None:
        """Return the stored error message for a failed job, if any."""
        job = self.repo.get_job(job_id)
        if job:
            error = job.get("error")
            return str(error) if error else None
        return None

    def fail_stale_queued(self) -> int:
        """Mark orphaned queued/processing rows as failed."""
        return self.repo.fail_stale_queued_jobs()
