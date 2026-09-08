"""Regression tests: old-schema DBs (no error_details column) must not break uploads.

Covers the `no such column: error_details` incident: code written against the
new schema ran on a DB file created before the column existed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.database.job_repository import JobRepository  # noqa: E402
from app.database.models import Database  # noqa: E402
from app.services.job_store import SQLiteJobStore  # noqa: E402

# Pre-migration shape: extraction_jobs WITHOUT error_details.
OLD_SCHEMA_SQL = """
CREATE TABLE extraction_jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    doc_type TEXT,
    language TEXT,
    extraction_source TEXT,
    completeness_score REAL DEFAULT 1.0,
    needs_review INTEGER DEFAULT 0,
    validation_errors TEXT DEFAULT '[]',
    error TEXT,
    failed_stage TEXT,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE extracted_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT,
    value_type TEXT DEFAULT 'string',
    confidence REAL DEFAULT 0.0,
    source_span TEXT,
    is_new_field INTEGER DEFAULT 0,
    FOREIGN KEY (job_id) REFERENCES extraction_jobs(id) ON DELETE CASCADE
);
CREATE TABLE judge_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    score REAL DEFAULT 0.0,
    issues TEXT DEFAULT '[]',
    notes TEXT DEFAULT '',
    FOREIGN KEY (job_id) REFERENCES extraction_jobs(id) ON DELETE CASCADE
);
"""


class _RawDB:
    """Database handle WITHOUT running migrations (simulates a stale process)."""

    def __init__(self, path: Path):
        self._path = str(path)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _make_old_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(OLD_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def _columns(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute("PRAGMA table_info(extraction_jobs)")]
    finally:
        conn.close()


def test_migration_adds_column_to_old_db(tmp_path):
    """Opening an old-schema file with Database() must add the column."""
    db_path = tmp_path / "old.db"
    _make_old_db(db_path)
    assert "error_details" not in _columns(db_path)

    Database(db_path)

    assert "error_details" in _columns(db_path)


def test_complete_job_falls_back_without_column(tmp_path):
    """complete_job on an unmigrated file must complete, dropping details."""
    db_path = tmp_path / "stale.db"
    _make_old_db(db_path)
    repo = JobRepository(_RawDB(db_path))  # no migration on purpose
    job_id = uuid4()
    repo.create_job(job_id, "receipt.jpg", "image/jpeg", 10)

    # Must not raise sqlite3.OperationalError("no such column: error_details").
    repo.complete_job(
        job_id=job_id,
        doc_type="invoice",
        language="en",
        extraction_source="ocr",
        completeness_score=0.0,
        needs_review=True,
        validation_errors=["Router failed: LLM API call failed (BadRequestError)"],
        fields=[],
        error_details={"stage": "router", "status": 400, "message": "Model not found"},
    )

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT status, doc_type FROM extraction_jobs WHERE id = ?", (str(job_id),)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "completed"
    assert row[1] == "invoice"


def test_error_details_roundtrip_on_migrated_db(tmp_path):
    """Migrated DBs persist and serve back error_details via the job store."""
    db_path = tmp_path / "new.db"
    store = SQLiteJobStore(db_path=str(db_path))
    job = store.create("receipt.jpg", "image/jpeg", 10)
    details = {"stage": "router", "provider": "opencode",
               "status": 400, "code": "model_not_found", "message": "Model not found"}
    store.save_result(job.job_id, {"documents": [{
        "doc_type": "invoice", "language": "en", "extraction_source": "ocr",
        "completeness_score": 0.0, "needs_review": True,
        "validation_errors": ["Router failed: x"], "fields": [],
        "error": "Router failed: x", "failed_stage": "router",
        "error_details": details,
    }]})

    fetched = store.get(job.job_id)
    assert fetched is not None and fetched.status == "completed"
    assert fetched.result["documents"][0]["error_details"] == details

    raw = json.loads(
        sqlite3.connect(str(db_path)).execute(
            "SELECT error_details FROM extraction_jobs WHERE id = ?", (str(job.job_id),)
        ).fetchone()[0]
    )
    assert raw["code"] == "model_not_found"
