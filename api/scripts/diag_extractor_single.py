#!/usr/bin/env python3
"""Phase 5 (PREPARED — DO NOT RUN without explicit authorization).

Single-dispatch Extractor LLM-path diagnostic for the failed local page:
  sroie_X51008142033.jpg / job 334f03b7-de2e-49d7-9c18-79ea243926cc

What it does (exactly one outbound Extractor HTTP attempt):
  1. Verifies source identity + OCR provenance from stored evidence
     (read-only): job row, page payload, source original bytes, stored
     OCR text/blocks. BLOCKED (exit 2, no request) unless all verify.
  2. Copies the invoice field catalog to a TEMPORARY directory (never the
     live catalog overlay) and builds an InvoiceExtractor over it.
  3. Fires ONE extractor call with process-local single-attempt controls:
       - client budget patched in-process to 1 attempt, 0 timeout retries
       - a dispatch-counting wrapper aborts any 2nd HTTP dispatch
       - no Router call, no Judge call, no format fallback, no corrective
         generation (a parse failure raises instead of regenerating)
     Deadlines (all finite, process-local): request timeout from effective
     settings, outer stage 150s, overall run 180s.
  4. Records a content-free report: effective settings (no secrets),
     model-loaded state, memory/swap observations, dispatch count,
     duration, usage/finish reason when available, exception chain on
     failure. Judge is explicitly unavailable; the outcome is
     completed-cache-ineligible by construction (nothing is written to
     any result cache, History DB, or source store).

THIS IS AN LLM-PATH DIAGNOSTIC, not an upload-to-result benchmark.
Isolated storage only: temp dirs under /tmp. No History writes, no
source writes, no downloads, no model/provider changes, no pushes.

Usage (operator, from repo root, venv active):
  python api/scripts/diag_extractor_single.py [--job-id ...] [--db ...] \\
      [--sources ...] [--out /tmp/diag_report.json]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))

DEFAULT_JOB = "334f03b7-de2e-49d7-9c18-79ea243926cc"
STAGE_DEADLINE_S = 150.0
OVERALL_DEADLINE_S = 180.0


def _meminfo() -> dict:
    out: dict = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            key = key.strip()
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                out[key] = rest.strip()
    except OSError:
        pass
    return out


def _ollama_ps(base_url: str) -> dict:
    """Local model-loaded state (read-only GET, no download, no inference)."""
    import urllib.request

    host = base_url.split("/v1")[0]
    try:
        with urllib.request.urlopen(host + "/api/ps", timeout=10) as resp:  # noqa: S310
            return {"reachable": True, "ps": json.loads(resp.read().decode())}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def verify_provenance(db_path: Path, sources_root: Path, job_id: str) -> dict:
    """Read-only provenance gate. Raises RuntimeError (blocked) on mismatch."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        job = con.execute(
            "SELECT filename, content_type, size_bytes FROM extraction_jobs WHERE id=?",
            (job_id,)).fetchone()
        if job is None:
            raise RuntimeError(f"job {job_id} not found in {db_path}")
        filename, content_type, size_bytes = job
        page = con.execute(
            "SELECT result_json FROM extraction_pages WHERE job_id=?",
            (job_id,)).fetchone()
        if page is None:
            raise RuntimeError("no stored page payload for job")
        doc = json.loads(page[0])
    finally:
        con.close()
    source = doc.get("source") or {}
    source_id = source.get("source_id")
    original = sources_root / str(source_id) / "original"
    if not original.is_file():
        raise RuntimeError(f"source original missing: {original}")
    raw = original.read_bytes()
    if len(raw) != size_bytes:
        raise RuntimeError(
            f"source size mismatch: disk {len(raw)} != job {size_bytes}")
    text = doc.get("full_text") or ""
    blocks = doc.get("ocr_blocks") or []
    if not text.strip() or not blocks:
        raise RuntimeError("stored OCR text/blocks absent: not an OCR-provenance page")
    return {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "source_id": str(source_id),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "text_chars": len(text),
        "block_count": len(blocks),
        "engines": sorted({b.get("engine") for b in blocks if b.get("engine")}),
        "failed_stage": doc.get("failed_stage"),
        "failed_error": doc.get("error"),
        "text": text,
    }


async def _single_dispatch(extractor, text: str, ledger: list) -> dict:
    import app.services.client as client_mod
    from app.services.request_control import collect_dispatches

    # Process-local single-attempt controls (nothing persisted).
    orig_attempts = client_mod.RATE_LIMIT_MAX_RETRIES
    orig_timeouts = client_mod.TIMEOUT_MAX_RETRIES
    client_mod.RATE_LIMIT_MAX_RETRIES = 1
    client_mod.TIMEOUT_MAX_RETRIES = 0
    create = extractor._client._client.chat.completions.create
    dispatches = {"n": 0}

    async def guarded_create(**kwargs):
        dispatches["n"] += 1
        if dispatches["n"] > 1:
            raise RuntimeError(
                "second HTTP dispatch blocked: single-attempt diagnostic")
        return await create(**kwargs)

    extractor._client._client.chat.completions.create = guarded_create
    started = time.perf_counter()
    try:
        with collect_dispatches() as events:
            try:
                async with asyncio.timeout(STAGE_DEADLINE_S):
                    call = await extractor.extract_call(text=text, page_number=1)
            finally:
                duration = time.perf_counter() - started
                # Extend inside `finally`: on exception the statement below
                # would be skipped and recorded events lost to the report
                # (observed 2026-09-16: empty attempt_events on timeout).
                ledger.extend(events)
        return {"call": call, "duration_s": round(duration, 3),
                "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"call": None,
                "duration_s": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "chain": [f"{type(e).__name__}: {str(e)[:200]}"
                          for e in _chain(exc)][:5]}
    finally:
        client_mod.RATE_LIMIT_MAX_RETRIES = orig_attempts
        client_mod.TIMEOUT_MAX_RETRIES = orig_timeouts
        extractor._client._client.chat.completions.create = create


def _chain(exc: BaseException):
    seen = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 8:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-dispatch Extractor LLM-path diagnostic (prepared, gated).")
    parser.add_argument("--job-id", default=DEFAULT_JOB)
    parser.add_argument("--db", default=str(REPO_ROOT / "data-local" / "extraction.db"))
    parser.add_argument("--sources", default=str(REPO_ROOT / "data-local" / "sources"))
    parser.add_argument("--out", default="/tmp/diag_extractor_single.json")
    args = parser.parse_args()

    report: dict = {"job_id": args.job_id, "status": "started",
                    "judge": "unavailable (by design)",
                    "cache_eligible": False}
    try:
        prov = verify_provenance(Path(args.db), Path(args.sources), args.job_id)
    except RuntimeError as exc:
        report.update({"status": "blocked", "block_reason": str(exc)})
        print(json.dumps(report, indent=2))
        return 2
    text = prov.pop("text")
    report["provenance"] = prov

    from app.agents.extractors import InvoiceExtractor
    from app.core.config import Settings
    from app.services.client import Client
    from app.services.field_catalog import FieldCatalog
    from app.services.request_control import summarize_dispatches

    settings = Settings()  # effective deployment config; secrets never printed
    report["settings"] = {
        "provider": settings.llm_provider,
        "endpoint": settings.llm_base_url,
        "model": settings.extraction_model_name,
        "extraction_max_tokens": settings.extraction_max_tokens,
        "request_timeout_s": settings.llm_request_timeout_seconds,
        "stage_deadline_s": STAGE_DEADLINE_S,
        "overall_deadline_s": OVERALL_DEADLINE_S,
        "region_extraction_enabled": settings.region_extraction_enabled,
        "compact_request": bool(getattr(settings, "extraction_compact_request", False) is True),
    }
    report["model_state_before"] = _ollama_ps(settings.llm_base_url)
    report["memory_before"] = _meminfo()

    tmp = Path(tempfile.mkdtemp(prefix="diag_extractor_"))
    try:
        live_catalog = Path(settings.knowledge_base_path) / "field_catalog"
        tmp_catalog = tmp / "field_catalog"
        shutil.copytree(live_catalog, tmp_catalog)
        catalog = FieldCatalog(tmp)
        extractor = InvoiceExtractor(
            settings, catalog, Client(settings))
        ledger: list = []
        try:
            outcome = await asyncio.wait_for(
                _single_dispatch(extractor, text, ledger),
                timeout=OVERALL_DEADLINE_S)
        except asyncio.TimeoutError:
            outcome = {"call": None, "duration_s": OVERALL_DEADLINE_S,
                       "error": "overall diagnostic deadline expired",
                       "chain": []}
        report["dispatches"] = summarize_dispatches(ledger, "extractor")
        report["attempt_events"] = [
            {"tier": e.tier, "purpose": e.purpose,
             "duration_s": round(e.duration_s, 3), "outcome": e.outcome}
            for e in ledger]
        if outcome["call"] is not None:
            call = outcome["call"]
            report.update({
                "status": "completed-one-dispatch",
                "duration_s": outcome["duration_s"],
                "fields": len(call.fields), "tables": len(call.tables),
                "new_fields": call.new_field_names,
            })
        else:
            report.update({"status": "failed-one-dispatch",
                           "duration_s": outcome["duration_s"],
                           "error": outcome["error"],
                           "exception_chain": outcome.get("chain", [])})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["model_state_after"] = _ollama_ps(settings.llm_base_url)
    report["memory_after"] = _meminfo()

    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] == "completed-one-dispatch" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
