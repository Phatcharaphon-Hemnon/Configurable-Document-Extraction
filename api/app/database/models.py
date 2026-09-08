"""SQLite database models and connection management."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = "data/extraction.db"


class Database:
    """SQLite database connection manager."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
        # Lightweight migration for pre-existing DBs, in its own connection:
        # executescript() above manages its own transaction state, so a
        # follow-up ALTER on the same handle is not guaranteed to persist.
        # Check PRAGMA instead of matching exception text (wording varies).
        # See docs/provider_errors.md.
        try:
            with self.connect() as conn:
                columns = [row["name"] for row in conn.execute("PRAGMA table_info(extraction_jobs)")]
                if "error_details" not in columns:
                    conn.execute("ALTER TABLE extraction_jobs ADD COLUMN error_details TEXT")
                    logger.info("Migrated %s: added error_details column", self.db_path)
        except Exception as exc:
            logger.warning("error_details migration skipped for %s: %s", self.db_path, exc)
        logger.info("Database initialized: %s", self.db_path)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with automatic commit/rollback."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# Database schema
SCHEMA_SQL = """
-- Extraction jobs table
CREATE TABLE IF NOT EXISTS extraction_jobs (
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
    error_details TEXT,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Extracted fields table (stores all fields for each job)
CREATE TABLE IF NOT EXISTS extracted_fields (
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

-- Judge results table
CREATE TABLE IF NOT EXISTS judge_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    score REAL DEFAULT 0.0,
    issues TEXT DEFAULT '[]',
    notes TEXT DEFAULT '',
    FOREIGN KEY (job_id) REFERENCES extraction_jobs(id) ON DELETE CASCADE
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_jobs_status ON extraction_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_extracted_at ON extraction_jobs(extracted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_doc_type ON extraction_jobs(doc_type);
CREATE INDEX IF NOT EXISTS idx_fields_job_id ON extracted_fields(job_id);
"""
