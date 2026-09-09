"""Backed-up, repeatable import of legacy SQLite history. See docs/page_storage.md."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.database.models import Database


def merge_history(target: Path, legacy: Path, backup_dir: Path) -> dict:
    """Call only with application writers stopped. Never overwrite existing job IDs."""
    target, legacy = target.resolve(), legacy.resolve()
    if target == legacy or not legacy.exists():
        return {"imported": 0}
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    for label, path in (("canonical", target), ("legacy", legacy)):
        if path.exists():
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as src:
                with sqlite3.connect(backup_dir / f"{stamp}-{label}.db") as dst:
                    src.backup(dst)
    db = Database(target)
    with sqlite3.connect(f"file:{legacy}?mode=ro", uri=True) as src, db.connect() as dst:
        src.row_factory = sqlite3.Row
        dst.execute("BEGIN IMMEDIATE")
        imported = 0
        for row in src.execute("SELECT * FROM extraction_jobs ORDER BY created_at"):
            item = dict(row)
            if dst.execute("SELECT 1 FROM extraction_jobs WHERE id=?", (item["id"],)).fetchone():
                continue
            columns = [r["name"] for r in dst.execute("PRAGMA table_info(extraction_jobs)") if r["name"] in item]
            dst.execute(
                f"INSERT INTO extraction_jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [item[c] for c in columns],
            )
            for table in ("extracted_fields", "judge_results", "extraction_pages"):
                if not src.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                for child in src.execute(f"SELECT * FROM {table} WHERE job_id=?", (item["id"],)):
                    values = {k: v for k, v in dict(child).items() if k != "id"}
                    dst.execute(
                        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        list(values.values()),
                    )
            imported += 1
        dst.execute(
            "INSERT INTO storage_imports(source_path,jobs_imported) VALUES (?,?) "
            "ON CONFLICT(source_path) DO UPDATE SET jobs_imported=jobs_imported+excluded.jobs_imported",
            (str(legacy), imported),
        )
        assert not dst.execute("PRAGMA foreign_key_check").fetchall(), "Foreign-key validation failed"
        count = dst.execute("SELECT COUNT(*) FROM extraction_jobs").fetchone()[0]
    return {"imported": imported, "total": count, "backups": str(backup_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--backups", type=Path, required=True)
    args = parser.parse_args()
    print(merge_history(args.target, args.legacy, args.backups))


if __name__ == "__main__":
    main()
