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
    progress: dict[str, Any] | None = None


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[UUID, JobRecord] = {}

    def create(self, filename: str = "unknown", content_type: str | None = None, size_bytes: int | None = None) -> JobRecord:
        job = JobRecord(job_id=uuid4(), status="queued")
        self._jobs[job.job_id] = job
        return job

    def set_progress(self, job_id: UUID, progress: dict[str, Any]) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].progress = progress

    def mark_processing(self, job_id: UUID) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].status = "processing"

    def save_result(self, job_id: UUID, result: dict[str, Any], *, status: str | None = None) -> JobRecord:
        job = self._jobs[job_id]
        job.status = status or ("failed" if result.get("error") and not result.get("documents") else "completed")
        job.result = result
        return job

    def get(self, job_id: UUID) -> JobRecord | None:
        return self._jobs.get(job_id)

    def fail_job(self, job_id: UUID, error: str) -> None:
        """Mark a job failed (e.g. cancelled background task)."""
        if job_id in self._jobs:
            self._jobs[job_id].status = "failed"
            self._jobs[job_id].result = {**(self._jobs[job_id].result or {"documents": []}), "error": error}

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

    def set_progress(self, job_id: UUID, progress: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE extraction_jobs SET progress_payload=? WHERE id=?",
                         (json.dumps(progress), str(job_id)))

    def save_result(self, job_id: UUID, result: dict[str, Any], *, status: str | None = None) -> JobRecord:
        """Atomically persist the complete response and every page; retain legacy summaries."""
        job = self.repo.get_job(job_id)
        payload = dict(result)
        payload.setdefault("request", {"filename": job["filename"],
                                       "content_type": job["content_type"],
                                       "size_bytes": job["size_bytes"]})
        payload["job_id"] = str(job_id)
        docs = payload.get("documents", [])
        status = status or ("completed" if docs else "failed")
        types = sorted({d.get("doc_type") for d in docs if not d.get("error")})
        languages = sorted({d.get("language") for d in docs if d.get("language")})
        errors = [e for d in docs for e in d.get("validation_errors", [])]
        with self.db.connect() as conn:
            conn.execute("""UPDATE extraction_jobs SET status=?, result_payload=?,
                doc_type=?, language=?, extraction_source=?, completeness_score=?,
                needs_review=?, validation_errors=?, error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""", (status, json.dumps(payload, ensure_ascii=False, default=str),
                types[0] if len(types) == 1 else "mixed" if types else None,
                languages[0] if len(languages) == 1 else "mixed" if languages else None,
                "ocr" if docs else None,
                sum(d.get("completeness_score", 0) for d in docs) / len(docs) if docs else 0,
                int(any(d.get("needs_review") or d.get("error") for d in docs)),
                json.dumps(errors, ensure_ascii=False), payload.get("error"), str(job_id)))
            if len(docs) == 1:
                conn.execute("UPDATE extraction_jobs SET error_details=? WHERE id=?", (
                    json.dumps(docs[0].get("error_details")) if docs[0].get("error_details") else None, str(job_id)))
            conn.execute("DELETE FROM extraction_pages WHERE job_id=?", (str(job_id),))
            conn.executemany("INSERT INTO extraction_pages VALUES (?, ?, ?)", [
                (str(job_id), i, json.dumps(d, ensure_ascii=False, default=str)) for i, d in enumerate(docs)
            ])
        return JobRecord(job_id=job_id, status=status, result=payload)

    def get(self, job_id: UUID) -> JobRecord | None:
        """Get a job from the database."""
        job = self.repo.get_job(job_id)
        if not job:
            return None

        if job.get("result_payload"):
            return JobRecord(job_id=job_id, status=job["status"],
                             result=json.loads(job["result_payload"]),
                             progress=json.loads(job["progress_payload"]) if job.get("progress_payload") else None)

        # Columns stored as JSON strings must be deserialized back, or the
        # FileExtractionResponse validation fails and polls see no result.
        validation_errors = job.get("validation_errors") or []
        if isinstance(validation_errors, str):
            try:
                validation_errors = json.loads(validation_errors)
            except ValueError:
                validation_errors = []
        error_details = job.get("error_details")
        if isinstance(error_details, str):
            try:
                error_details = json.loads(error_details)
            except ValueError:
                error_details = None
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
                "error_details": error_details,
            }]
        }

        return JobRecord(
            job_id=job_id,
            status=job.get("status", "unknown"),
            result=result if job.get("status") == "completed" else None,
            progress=json.loads(job["progress_payload"]) if job.get("progress_payload") else None,
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
