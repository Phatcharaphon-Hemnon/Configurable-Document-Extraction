"""Job repository for extraction job CRUD operations."""

from __future__ import annotations

import json
import logging
import sqlite3
from uuid import UUID

from app.database.models import Database

logger = logging.getLogger(__name__)


class JobRepository:
    """Repository for extraction job CRUD operations."""

    def __init__(self, db: Database):
        self.db = db

    def create_job(
        self,
        job_id: UUID,
        filename: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
    ) -> dict:
        """Create a new extraction job."""
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO extraction_jobs (id, filename, content_type, size_bytes, status)
                VALUES (?, ?, ?, ?, 'queued')
                """,
                (str(job_id), filename, content_type, size_bytes),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: UUID) -> dict | None:
        """Get a job by ID with its fields and judge result."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_jobs WHERE id = ?",
                (str(job_id),),
            ).fetchone()

            if not row:
                return None

            job = dict(row)

            # Get fields
            fields = conn.execute(
                "SELECT * FROM extracted_fields WHERE job_id = ?",
                (str(job_id),),
            ).fetchall()
            job["fields"] = [dict(f) for f in fields]

            # Get judge result
            judge = conn.execute(
                "SELECT * FROM judge_results WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
            job["judge"] = dict(judge) if judge else None

            return job

    def update_job_status(
        self,
        job_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update job status."""
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE extraction_jobs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error, str(job_id)),
            )

    def fail_stale_queued_jobs(self, error: str = "Interrupted (server restarted or request cancelled)") -> int:
        """Mark queued and processing jobs interrupted by restart as failed.

        Called once at startup: any pending row cannot have a live
        worker (workers are in-process tasks), so it is an orphan.
        Returns the number of rows marked.
        """
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('queued', 'processing')
                """,
                (error,),
            )
            return cursor.rowcount

    def complete_job(
        self,
        job_id: UUID,
        doc_type: str,
        language: str | None,
        extraction_source: str | None,
        completeness_score: float,
        needs_review: bool,
        validation_errors: list[str],
        fields: list[dict],
        judge_result: dict | None = None,
        error_details: dict | None = None,
    ) -> None:
        """Complete a job with extraction results."""
        with self.db.connect() as conn:
            # Update job. Tolerate pre-migration DB files lacking the
            # error_details column: degrade (drop details) instead of failing
            # the whole upload with "no such column".
            try:
                self._update_job_completion(
                    conn, job_id, doc_type, language, extraction_source,
                    completeness_score, needs_review, validation_errors,
                    error_details, include_error_details=True,
                )
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "no such column" in message and "error_details" in message:
                    logger.warning(
                        "job %s: error_details column missing, saving without provider details",
                        job_id,
                    )
                    self._update_job_completion(
                        conn, job_id, doc_type, language, extraction_source,
                        completeness_score, needs_review, validation_errors,
                        None, include_error_details=False,
                    )
                else:
                    raise

            # Insert fields
            for field in fields:
                value = field.get("value")
                value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value) if value is not None else None
                conn.execute(
                    """
                    INSERT INTO extracted_fields
                    (job_id, name, value, value_type, confidence, source_span, is_new_field)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(job_id),
                        field.get("name"),
                        value_str,
                        type(value).__name__ if value is not None else "null",
                        field.get("confidence", 0.0),
                        field.get("source_span"),
                        int(field.get("is_new_field", False)),
                    ),
                )

            # Insert judge result if present
            if judge_result:
                conn.execute(
                    """
                    INSERT INTO judge_results (job_id, score, issues, notes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(job_id),
                        judge_result.get("score", 0.0),
                        json.dumps(judge_result.get("issues", [])),
                        judge_result.get("notes", ""),
                    ),
                )

    @staticmethod
    def _update_job_completion(
        conn,
        job_id: UUID,
        doc_type: str,
        language: str | None,
        extraction_source: str | None,
        completeness_score: float,
        needs_review: bool,
        validation_errors: list[str],
        error_details: dict | None,
        *,
        include_error_details: bool,
    ) -> None:
        """Run the job-completion UPDATE, optionally without error_details."""
        if include_error_details:
            conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'completed',
                    doc_type = ?,
                    language = ?,
                    extraction_source = ?,
                    completeness_score = ?,
                    needs_review = ?,
                    validation_errors = ?,
                    error_details = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    doc_type,
                    language,
                    extraction_source,
                    completeness_score,
                    int(needs_review),
                    json.dumps(validation_errors),
                    json.dumps(error_details) if error_details else None,
                    str(job_id),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'completed',
                    doc_type = ?,
                    language = ?,
                    extraction_source = ?,
                    completeness_score = ?,
                    needs_review = ?,
                    validation_errors = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    doc_type,
                    language,
                    extraction_source,
                    completeness_score,
                    int(needs_review),
                    json.dumps(validation_errors),
                    str(job_id),
                ),
            )

    def list_jobs(
        self,
        status: str | None = None,
        doc_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List jobs with filtering and pagination.

        Returns:
            Tuple of (jobs list, total count).
        """
        with self.db.connect() as conn:
            # Build query
            where_clauses = []
            params = []

            if status:
                where_clauses.append("status = ?")
                params.append(status)
            if doc_type:
                where_clauses.append("doc_type = ?")
                params.append(doc_type)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # Get total count
            count_row = conn.execute(
                f"SELECT COUNT(*) as total FROM extraction_jobs {where_sql}",
                params,
            ).fetchone()
            total = count_row["total"] if count_row else 0

            # Get jobs
            rows = conn.execute(
                f"""
                SELECT * FROM extraction_jobs
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

            jobs = [dict(row) for row in rows]

            return jobs, total

    def delete_job(self, job_id: UUID) -> bool:
        """Delete a job and its related data."""
        with self.db.connect() as conn:
            result = conn.execute(
                "DELETE FROM extraction_jobs WHERE id = ?",
                (str(job_id),),
            )
            return result.rowcount > 0

    def get_stats(self) -> dict:
        """Get extraction statistics."""
        with self.db.connect() as conn:
            stats = {}

            # Total jobs
            row = conn.execute("SELECT COUNT(*) as total FROM extraction_jobs").fetchone()
            stats["total_jobs"] = row["total"] if row else 0

            # Jobs by status
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM extraction_jobs GROUP BY status"
            ).fetchall()
            stats["by_status"] = {row["status"]: row["count"] for row in rows}

            # Jobs by doc type
            rows = conn.execute(
                "SELECT doc_type, COUNT(*) as count FROM extraction_jobs WHERE doc_type IS NOT NULL GROUP BY doc_type"
            ).fetchall()
            stats["by_doc_type"] = {row["doc_type"]: row["count"] for row in rows}

            # Average completeness
            row = conn.execute(
                "SELECT AVG(completeness_score) as avg_score FROM extraction_jobs WHERE status = 'completed'"
            ).fetchone()
            stats["avg_completeness"] = row["avg_score"] if row and row["avg_score"] else 0.0

            # Jobs needing review
            row = conn.execute(
                "SELECT COUNT(*) as count FROM extraction_jobs WHERE needs_review = 1"
            ).fetchone()
            stats["needs_review"] = row["count"] if row else 0

            return stats
