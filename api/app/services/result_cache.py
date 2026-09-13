"""Persistent completed-result cache (SQLite, no Redis).

Caches COMPLETED page results (including legitimate review outcomes) under
the project cache directory. The existing OCR cache is kept separate.

Eligibility (cacheable only):
- No error / no failed_stage (excludes cancellations, transient provider
  failures, incomplete processing).
- judge_status != "unavailable" (unavailable Judge never cached).
- A deliberately skipped Judge is cacheable only when the strengthened skip
  policy completed successfully (judge_status == "skipped").

Fingerprint covers every result-affecting input (no credentials):
file bytes + filename context, page number, OCR engines/model hashes/settings,
provider endpoint + stage models, generation settings, prompts/schemas,
catalog contents, enabled examples/retrieval inputs, and policy versions.
Any relevant code/config/prompt/model/catalog change invalidates via a
different fingerprint. Catalog mutation is explicit: a result is never reused
under a fingerprint differing from the inputs actually used.

Storage: atomic SQLite writes (WAL + transactions), configurable TTL
(default 7 days), max entries (default 128 page entries), deterministic
eviction (oldest computed_at first). Cached payloads are validated against
current contracts; corrupt/expired entries are treated as misses and removed.

On reuse: the caller creates a normal new submission record with current
source references and filenames. Prior job IDs, download/preview URLs are
never reused. Original computation timestamps + stage timings are preserved
as historical metadata; current lookup/remapping latency is reported
separately. Full/partial/miss status is exposed via response timings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS result_cache (
    fingerprint TEXT PRIMARY KEY,
    computed_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    payload TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_result_cache_expires ON result_cache(expires_at);
"""

# Version pins for every result-affecting layer. Bump when the corresponding
# code changes so fingerprints invalidate deterministically.
EXTRACTOR_VERSION = "extractor-v2-typed"
VALIDATOR_VERSION = "validator-v2-acceptance"
JUDGE_VERSION = "judge-v2-structured"
# v3: extraction output contract now requires ONE root {fields,tables} with an
# explicit table-inside-tables rule + compact structural example (fixes the
# qwen2.5:3b table-as-root failure); client uses classified recovery with at
# most one corrective generation (no blind json_object+plain regeneration).
PROMPT_VERSION = "prompts-v3-classified"
# Client recovery policy (classified parse kinds, table-root normalization,
# truncation handling). Bumped with the prompt contract — both invalidate.
CLIENT_RECOVERY_VERSION = "client-recovery-v2-classified"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_json(obj: Any) -> str:
    return _sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def catalog_fingerprint(catalog) -> str:
    """Hash of all three catalog files (contents, not mtimes)."""
    try:
        parts = []
        for doc_type in ("invoice", "purchase_order", "delivery_note"):
            try:
                path = catalog._path_for(doc_type)
                parts.append(path.read_bytes() if path.is_file() else b"")
            except Exception:
                parts.append(b"")
        return _sha256(b"\x00".join(parts))
    except Exception:
        return "unknown"


def is_cacheable_result(result) -> tuple[bool, str]:
    """Whether a completed page result may be cached (with reason)."""
    try:
        if getattr(result, "error", None):
            return False, "page has error"
        if getattr(result, "failed_stage", None):
            return False, f"failed stage {getattr(result, 'failed_stage', None)}"
        status = getattr(result, "judge_status", None)
        if status == "unavailable":
            return False, "judge unavailable"
        if status not in ("passed", "flagged", "skipped"):
            return False, f"unexpected judge status {status}"
        return True, "cacheable"
    except Exception as exc:
        return False, f"eligibility check failed: {exc}"


class ResultCache:
    def __init__(self, settings) -> None:
        self.settings = settings
        base = Path(getattr(settings, "cache_path", ".cache"))
        self.path = base / "result-cache.sqlite"
        self.ttl_seconds = float(getattr(settings, "result_cache_ttl_seconds", 7 * 24 * 3600))
        self.max_entries = int(getattr(settings, "result_cache_max_entries", 128))
        self.enabled = bool(getattr(settings, "result_cache_enabled", True))
        self._init()

    @property
    def enabled_and_ready(self) -> bool:
        return bool(self.enabled)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        try:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA_SQL)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Result cache init skipped: %s", exc)

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------

    def fingerprint_page(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        page_number: int,
        page_text: str,
        ocr_engine: str,
        ocr_languages: str,
        ocr_dpi: int,
        ocr_model_hashes: dict,
        hybrid_fingerprint: dict,
    ) -> str:
        s = self.settings
        catalog_hash = catalog_fingerprint(getattr(s, "_catalog_ref", None) or self._catalog())
        try:
            from app.core.security import COHERENCE_THRESHOLD as _coh_thr
        except Exception:
            _coh_thr = 0.40
        core = {
            "v": 3,
            "file_sha": _sha256(file_bytes),
            "filename": Path(filename or "").name,
            "page_number": page_number,
            "page_text_sha": _sha256((page_text or "").encode("utf-8")),
            "ocr": {
                "engine": ocr_engine,
                "languages": ocr_languages,
                "dpi": ocr_dpi,
                "models": ocr_model_hashes or {},
                "hybrid": hybrid_fingerprint or {},
                # OCR/recovery policy changes invalidate relevant caches.
                "coherence_threshold": float(_coh_thr),
            },
            "provider": {
                "endpoint": getattr(s, "llm_base_url", ""),
                "router_model": getattr(s, "router_model_name", ""),
                "extraction_model": getattr(s, "extraction_model_name", ""),
                "judge_model": getattr(s, "judge_model_name", ""),
                "temperature": getattr(s, "llm_temperature", 0.0),
                "extraction_max_tokens": getattr(s, "extraction_max_tokens", 0),
                "router_max_tokens": getattr(s, "router_max_tokens", 0),
                "router_text_chars": getattr(s, "router_text_chars", 0),
                "strict_schema": not bool(getattr(s, "disable_strict_json_schema", False)),
                "few_shot": int(getattr(s, "few_shot_examples_per_doc_type", 0)),
                "judge_skip": bool(getattr(s, "judge_skip_when_clean", True)),
                "judge_skip_conf": float(getattr(s, "judge_skip_confidence", 0.85)),
            },
            "versions": {
                "prompts": PROMPT_VERSION,
                "client_recovery": CLIENT_RECOVERY_VERSION,
                "extractor": EXTRACTOR_VERSION,
                "validator": VALIDATOR_VERSION,
                "judge": JUDGE_VERSION,
                "acceptance": self._acceptance_version(),
                "catalog_sha": catalog_hash,
            },
        }
        return _hash_json(core)

    def _catalog(self):
        try:
            from pathlib import Path as _P

            from app.services.knowledge_base import KnowledgeBaseRepository

            return KnowledgeBaseRepository(_P(self.settings.knowledge_base_path)).catalog
        except Exception:
            return None

    @staticmethod
    def _acceptance_version() -> str:
        try:
            from app.schemas.documents import ACCEPTANCE_POLICY_VERSION

            return ACCEPTANCE_POLICY_VERSION
        except Exception:
            return "v1.0.0"

    # ------------------------------------------------------------------
    # Get / put
    # ------------------------------------------------------------------

    def get(self, fingerprint: str):
        """Return (ExtractionResult, meta) on hit, else (None, {'reason': ...})."""
        from app.schemas.documents import ExtractionResult

        if not self.enabled:
            return None, {"reason": "disabled"}
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT payload, meta, computed_at, expires_at FROM result_cache WHERE fingerprint=?",
                    (fingerprint,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            return None, {"reason": f"lookup failed: {exc}"}
        if row is None:
            return None, {"reason": "miss"}
        now = time.time()
        if float(row["expires_at"]) <= now:
            self._remove(fingerprint)
            return None, {"reason": "expired"}
        try:
            result = ExtractionResult.model_validate_json(row["payload"])
            meta = json.loads(row["meta"] or "{}")
            meta["computed_at"] = row["computed_at"]
            return result, meta
        except Exception as exc:
            self._remove(fingerprint)
            logger.warning("Result cache corrupt entry removed: %s", exc)
            return None, {"reason": f"corrupt: {exc}"}

    def put(self, fingerprint: str, result, meta: dict | None = None) -> bool:
        ok, reason = is_cacheable_result(result)
        if not self.enabled or not ok:
            return False
        try:
            from app.schemas.documents import ExtractionResult

            if not isinstance(result, ExtractionResult):
                result = ExtractionResult.model_validate(result)
            # Strip per-submission bindings before storing: source, ids, urls,
            # current timings/usage are request-scoped. Historical timings are
            # kept inside meta.original_timings instead.
            try:
                _orig_timings = dict(getattr(result, "timings", None) or {})
            except Exception:
                _orig_timings = {}
            try:
                _computed_iso = result.extracted_at.isoformat() if getattr(result, "extracted_at", None) else None
            except Exception:
                _computed_iso = None
            storable = result.model_copy(
                update={
                    "source": None,
                    "ocr_blocks": [],
                    "full_text": None,
                    "timings": {},
                    "usage": {},
                    "cache_metadata": None,
                }
            )
            payload = storable.model_dump_json()
            now = time.time()
            meta_out = dict(meta or {})
            meta_out.setdefault("acceptance_policy", self._acceptance_version())
            meta_out["original_timings"] = _orig_timings
            if _computed_iso:
                meta_out["computed_at_iso"] = _computed_iso
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO result_cache (fingerprint, computed_at, expires_at, payload, meta)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (fingerprint, now, now + self.ttl_seconds, payload, json.dumps(meta_out, ensure_ascii=False, default=str)),
                    )
                    # Deterministic eviction: oldest computed_at first.
                    count = conn.execute("SELECT COUNT(*) AS n FROM result_cache").fetchone()["n"]
                    if count > self.max_entries:
                        conn.execute(
                            "DELETE FROM result_cache WHERE fingerprint IN ("
                            "SELECT fingerprint FROM result_cache ORDER BY computed_at ASC LIMIT ?)",
                            (count - self.max_entries,),
                        )
            finally:
                conn.close()
            return True
        except Exception as exc:
            logger.warning("Result cache write skipped: %s", exc)
            return False

    def _remove(self, fingerprint: str) -> None:
        try:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("DELETE FROM result_cache WHERE fingerprint=?", (fingerprint,))
            finally:
                conn.close()
        except Exception:
            pass

    def clear(self) -> int:
        try:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute("DELETE FROM result_cache")
                    return cur.rowcount
            finally:
                conn.close()
        except Exception:
            return 0

    def stats(self) -> dict:
        try:
            conn = self._connect()
            try:
                n = conn.execute("SELECT COUNT(*) AS n FROM result_cache").fetchone()["n"]
                return {"entries": int(n), "path": str(self.path), "enabled": self.enabled}
            finally:
                conn.close()
        except Exception as exc:
            return {"entries": 0, "path": str(self.path), "enabled": self.enabled, "error": str(exc)}
