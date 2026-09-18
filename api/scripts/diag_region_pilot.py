#!/usr/bin/env python3
"""Region pilot harness (PREPARED — DO NOT RUN without explicit authorization).

Operator-only pilot for the opt-in region path on ONE provenance-verified
page. Unlike the full-page A/B (`docs/reports/compact_request_ab_2026-09-16.md`
— full-page Extractor calls only, never the region path), this harness drives
the ACTUAL region orchestration
(`DocumentExtractionService._extract_page_with_regions`: split → per-region
extract with checkpoints → merge → reconcile → shared validator + Judge
finish), sequentially under the existing caps.

What it does:
  1. Provenance gate (read-only, exit 2 with no request on mismatch): job
     row + page payload + source original bytes + stored OCR text/blocks.
     Dataset annotations (box/entity transcripts) are NEVER read as input.
  2. Builds an isolated service: temp catalog copy (live overlay untouched),
     temp SQLite history DB (checkpoints land here, never live History),
     temp sources/cache dirs, completed-result cache OFF.
  3. Runs ONE page through the real region path with process-local flag
     overrides only (production defaults untouched):
     `region_extraction_enabled=True` (+ `region_compact_request` per CLI).
     Sequential requests only (effective `LLM_MAX_CONCURRENT_REQUESTS`
     recorded; the script issues no concurrent calls).
  4. Records a content-free report: effective settings (names + numerics, no
     secrets), every actual region request shape (chars + sha256, never
     bodies), per-region checkpoint outcomes, first deterministically
     validated usable region vs final page completion, failures/unresolved/
     coverage/evidence findings, Judge inclusion + invariant check, budget
     accounting from actual HTTP-boundary dispatches.

Bounds (all finite, process-local): request timeout + stage deadlines from
effective settings; harness overall deadline `--overall-deadline`
(default 1800s: worst-case page 24 dispatches x ~(45s + backoff) + router/
judge stages + overhead — a bound, never a speed target). Judge is included
by construction (shared `_finish_page`); omitted/unavailable Judge must never
read as passed (invariant checked, not assumed).

Isolation: temp dirs under /tmp (or $TMPDIR); nothing written to live
History DB, sources, result caches, or the live catalog; report to
`--out` (/tmp default) + stdout only.

Usage (operator, from repo root, venv active):
  python api/scripts/diag_region_pilot.py --job-id <uuid> [--doc-type invoice]
      [--region-contracts / --no-region-contracts] [--out /tmp/diag_region_pilot.json]
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
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import diag_extractor_single as single  # noqa: E402 (provenance gate reuse)

DEFAULT_JOB = single.DEFAULT_JOB
OVERALL_DEADLINE_S = 1800.0


def sha_text(text: str) -> str:
    """Content-free shape identity (never log the body itself)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_expected_shapes(blocks, page_text: str, page_number: int, *, enabled: bool) -> list[dict]:
    """Rebuild expected region request shapes offline (no inference).

    Uses the same splitter + request builder the service uses, so the live
    run's recorded shapes can be cross-checked deterministically.
    Returns [{id, kind, block_count, chars, sha}] in region order.
    """
    from app.services.region_requests import build_region_request_text
    from app.services.regions import split_page

    split = split_page(list(blocks or []), page_text, page_number)
    shapes = []
    for region in split.regions:
        task_text = build_region_request_text(region, enabled=enabled)
        shapes.append({
            "id": region.id,
            "kind": region.kind,
            "block_count": len(region.block_ids),
            "chars": len(task_text),
            "sha": sha_text(task_text),
        })
    return shapes


def match_shapes(expected: list[dict], observed: list[dict]) -> list[dict]:
    """Cross-check rebuilt shapes against actually-sent request shapes.

    `observed` entries: {seq, chars, sha} in call order. Matching is by
    order + chars + sha; a mismatch means the live request differed from
    the deterministic rebuild (report, never silently accept).
    """
    matched = []
    for index, exp in enumerate(expected):
        obs = observed[index] if index < len(observed) else None
        ok = bool(obs and obs.get("chars") == exp["chars"] and obs.get("sha") == exp["sha"])
        matched.append({
            "id": exp["id"], "kind": exp["kind"], "block_count": exp["block_count"],
            "expected_chars": exp["chars"], "expected_sha": exp["sha"],
            "observed_chars": obs.get("chars") if obs else None,
            "observed_sha": obs.get("sha") if obs else None,
            "shape_match": ok,
        })
    if len(observed) > len(expected):
        for obs in observed[len(expected):]:
            matched.append({"id": None, "kind": None, "shape_match": False,
                            "observed_chars": obs.get("chars"),
                            "observed_sha": obs.get("sha"),
                            "note": "extra live dispatch beyond rebuilt regions"})
    return matched


def first_validated_usable(region_order: list[str], completed: dict, validator,
                            doc_type: str, region_texts: dict[str, str],
                            page_number: int = 1) -> dict | None:
    """First region whose output the deterministic validator accepts usable.

    `completed`: {region_id: {"fields": [...dicts...], "tables": [...dicts...]}}
    from isolated checkpoints, in service completion order filtered by
    `region_order` (split order). Usable = validator accepts >= 1 field or
    table (rejected-only regions are not usable, even when the LLM call
    "succeeded"). Deterministic post-hoc: no LLM, no Judge, region text only
    as the evidence context. Returns None when no region is usable.
    """
    from app.schemas.documents import ExtractedField, ExtractedTable

    for region_id in region_order:
        payload = completed.get(region_id)
        if not payload:
            continue
        try:
            fields = [ExtractedField.model_validate(f) for f in payload.get("fields", [])]
            tables = [ExtractedTable.model_validate(t) for t in payload.get("tables", [])]
        except Exception:
            continue
        try:
            (_errors, _completeness, _needs_review, accepted_fields,
             accepted_tables, _rejected, _issues) = validator.validate_detailed(
                doc_type=doc_type, fields=fields, tables=tables,
                document_text=region_texts.get(region_id),
                blocks=[], page_number=page_number, ocr_uncertain=False)
        except Exception:
            continue
        if accepted_fields or accepted_tables:
            return {"region_id": region_id,
                    "accepted_fields": len(accepted_fields),
                    "accepted_tables": len(accepted_tables)}
    return None


def check_budget(total_dispatches: int, page_cap: int, job_cap: int) -> dict:
    """Actual HTTP-boundary total vs the existing finite caps (advisory)."""
    within = total_dispatches <= page_cap and total_dispatches <= job_cap
    message = ""
    if not within:
        binding = (f"page ceiling {page_cap}" if total_dispatches > page_cap
                   else f"job ceiling {job_cap}")
        message = (f"pilot total {total_dispatches} dispatches exceeds {binding}; "
                   "caps/deadlines unchanged — record and report, never raise caps")
    return {"total_dispatches": total_dispatches, "page_cap": page_cap,
            "job_cap": job_cap, "within_caps": within, "message": message}


def check_judge_invariant(judge_status: str, judge_present: bool) -> dict:
    """Omitted/unavailable Judge must never read as passed.

    Violation iff the outcome claims `passed` with no Judge result object.
    `skipped`/`unavailable`/`flagged` are honest non-pass states by design.
    """
    violated = judge_status == "passed" and not judge_present
    return {"judge_status": judge_status, "judge_present": judge_present,
            "invariant_ok": not violated,
            "message": "" if not violated else
            "JUDGE-INVARIANT-VIOLATED: status passed with no Judge result"}


def load_page_payload(db_path: Path, job_id: str) -> dict:
    """Read-only stored page payload (provenance-verified text/blocks)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT result_json FROM extraction_pages WHERE job_id=?", (job_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError("no stored page payload for job")
    return json.loads(row[0])


async def run_pilot(args) -> dict:
    from app.schemas.ocr import OCRBlock, OCRPage

    report: dict = {"job_id": args.job_id, "status": "started",
                    "judge": "included (shared finish path)",
                    "cache_eligible": False}
    try:
        prov = single.verify_provenance(Path(args.db), Path(args.sources), args.job_id)
    except RuntimeError as exc:
        return {**report, "status": "blocked", "block_reason": str(exc)}
    text = prov.pop("text")
    report["provenance"] = prov

    payload = load_page_payload(Path(args.db), args.job_id)
    blocks = [OCRBlock.model_validate(b) for b in (payload.get("ocr_blocks") or [])]
    if len(blocks) != prov["block_count"] or (payload.get("full_text") or "") != text:
        return {**report, "status": "blocked",
                "block_reason": "stored payload drifted from provenance gate (no request issued)"}

    from app.core.config import Settings
    from app.services.extraction_service import DocumentExtractionService
    from app.services.region_requests import REGION_REQUEST_VERSION
    from app.services.regions import (
        MAX_REGION_DISPATCHES_PER_JOB,
        MAX_REGION_DISPATCHES_PER_PAGE,
        SPLITTER_VERSION,
    )

    settings = Settings()  # effective deployment config; secrets never printed
    tmp = Path(tempfile.mkdtemp(prefix="diag_region_pilot_"))
    try:
        live_catalog = Path(settings.knowledge_base_path) / "field_catalog"
        if not live_catalog.is_dir():
            return {**report, "status": "blocked",
                    "block_reason": f"live catalog missing: {live_catalog}"}
        shutil.copytree(live_catalog, tmp / "kb" / "field_catalog")
        # Process-local overrides only (production defaults untouched):
        settings.knowledge_base_path = str(tmp / "kb")
        settings.database_enabled = True
        settings.database_path = str(tmp / "history.db")
        settings.source_storage_path = str(tmp / "sources")
        settings.cache_path = str(tmp / "cache")
        settings.result_cache_enabled = False  # no completed-result writes
        settings.region_extraction_enabled = True  # default false in production
        settings.region_compact_request = bool(args.region_contracts)
        service = DocumentExtractionService(settings=settings)

        report["settings"] = {
            "provider": settings.llm_provider,
            "endpoint": settings.llm_base_url,
            "router_model": settings.router_model_name,
            "extraction_model": settings.extraction_model_name,
            "judge_model": settings.judge_model_name,
            "extraction_max_tokens": settings.extraction_max_tokens,
            "request_timeout_s": settings.llm_request_timeout_seconds,
            "router_timeout_s": settings.router_timeout_seconds,
            "extractor_timeout_s": settings.extractor_timeout_seconds,
            "judge_timeout_s": settings.judge_timeout_seconds,
            "max_concurrent_requests": settings.llm_max_concurrent_requests,
            "region_extraction_enabled": True,
            "region_extraction_enabled_override": "process-local (production default false)",
            "region_compact_request": bool(args.region_contracts),
            "extraction_compact_request": bool(
                getattr(settings, "extraction_compact_request", False) is True),
            "splitter_version": SPLITTER_VERSION,
            "region_request_version": REGION_REQUEST_VERSION,
            "page_cap": MAX_REGION_DISPATCHES_PER_PAGE,
            "job_cap": MAX_REGION_DISPATCHES_PER_JOB,
            "overall_deadline_s": float(args.overall_deadline),
            "doc_type": args.doc_type or "(router classifies; counted)",
        }
        report["model_state_before"] = single._ollama_ps(settings.llm_base_url)
        report["memory_before"] = single._meminfo()

        # Record every actual region request shape (chars + sha only).
        observed: list[dict] = []
        for ext in service.extractors.values():
            orig = ext.extract_call

            async def _recording(text, image_bytes=None, image_media_type=None,
                                 few_shot=None, page_number=1, _orig=orig, **kwargs):
                observed.append({"seq": len(observed),
                                 "chars": len(text or ""),
                                 "sha": sha_text(text or "")})
                return await _orig(text, image_bytes=image_bytes,
                                   image_media_type=image_media_type,
                                   few_shot=few_shot, page_number=page_number,
                                   **kwargs)

            ext.extract_call = _recording  # type: ignore[method-assign]

        ocr_page = OCRPage(text=text, blocks=blocks,
                           engine=(payload.get("ocr_blocks") or [{}])[0].get("engine", "tesseract")
                           if payload.get("ocr_blocks") else "tesseract")
        job = service.job_store.create(filename=prov["filename"],
                                       content_type=prov.get("content_type"),
                                       size_bytes=prov["size_bytes"])
        progress: dict = {"dispatches": 0}
        # OCR uncertainty from stored block reasons (same signal the merge
        # layer uses; page-level review_reasons are not stored on results).
        ocr_uncertain = any(getattr(b, "review_reason", None) for b in blocks)
        t0 = time.perf_counter()
        try:
            outcome = await asyncio.wait_for(
                service._extract_page_with_regions(
                    job_id=job.job_id, filename=prov["filename"], page_text=text,
                    ocr_page=ocr_page, ocr_uncertain_page=ocr_uncertain,
                    doc_type=args.doc_type, page_index=1, progress=progress,
                    cache_on=False, region_cache=True, job_started=t0,
                    source_key=f"{prov['filename']}:{prov['source_sha256'][:16]}"),
                timeout=float(args.overall_deadline))
            page_total_s = round(time.perf_counter() - t0, 3)
        except asyncio.TimeoutError:
            page_total_s = round(time.perf_counter() - t0, 3)
            return {**report, "status": "overall-deadline-expired",
                    "page_total_s": page_total_s,
                    "observed_region_requests": observed}
        document = outcome.document
        usage = dict(document.usage or {})
        total_dispatches = sum(int((u or {}).get("attempts", 0) or 0) for u in usage.values())

        # Offline deterministic rebuild of expected shapes (no inference).
        expected = build_expected_shapes(blocks, text, 1, enabled=bool(args.region_contracts))
        report["region_request_shapes"] = match_shapes(expected, observed)

        # Isolated checkpoints: per-region outcomes + coverage record.
        checkpoints = service.job_store.get_regions(job.job_id)
        by_id = {c.get("region_id"): c for c in checkpoints}
        sections = [s for s in (progress.get("sections") or [])]
        region_rows = []
        completed_payloads: dict[str, dict] = {}
        region_texts = {s["id"]: s.get("text", "") for s in
                        (lambda: _region_texts(blocks, text))()}
        for shape in expected:
            rid = shape["id"]
            cp = by_id.get(rid) or {}
            cp_payload = cp.get("payload") or {}
            if cp.get("status") == "completed":
                completed_payloads[rid] = cp_payload
            disp = cp_payload.get("dispatches") or {}
            region_rows.append({
                "region_id": rid, "kind": shape["kind"],
                "status": cp.get("status", "missing"),
                "seconds": round(float(cp_payload.get("seconds", 0.0) or 0.0), 3),
                "attempts": int(cp_payload.get("attempts", 0) or 0),
                "dispatches": {k: int(v or 0) for k, v in disp.items()} if disp else {},
                "prompt_chars": int(cp_payload.get("prompt_chars", 0) or 0),
                "detail": str((next((s.get("detail") for s in sections
                                     if s.get("region_id") == rid), "")
                               or cp_payload.get("error", ""))[:300]),
            })
        coverage = by_id.get("page-1-coverage", {}).get("payload") or {}
        report["regions"] = region_rows
        report["coverage"] = {
            "region_ids": coverage.get("region_ids") or [s["id"] for s in expected],
            "unresolved": coverage.get("unresolved") or [],
            "ambiguous_boundaries": coverage.get("ambiguous") or [],
            "tables_expected": coverage.get("tables_expected"),
            "region_errors": [str(e)[:300] for e in (coverage.get("region_errors") or [])],
        }

        # First validated usable region (deterministic post-hoc) vs page completion.
        doc_type = document.doc_type
        usable = first_validated_usable(
            [s["id"] for s in expected], completed_payloads,
            service.validator, doc_type, region_texts, page_number=1)
        report["first_region_elapsed_s"] = outcome.first_region_elapsed
        report["first_validated_usable_region"] = usable
        report["page_total_s"] = page_total_s

        judge_usage = usage.get("judge") or {}
        judge_dispatches = int(judge_usage.get("attempts", 0) or 0)
        report["judge"] = {
            "included": True,
            "status": document.judge_status,
            "dispatches": judge_dispatches,
            **check_judge_invariant(document.judge_status, document.judge is not None),
        }
        router_usage = usage.get("router") or {}
        report["router_dispatches"] = int(router_usage.get("attempts", 0) or 0)
        report["budget"] = check_budget(total_dispatches,
                                       MAX_REGION_DISPATCHES_PER_PAGE,
                                       MAX_REGION_DISPATCHES_PER_JOB)
        report["outcome"] = {
            "path_detail": outcome.path_detail,
            "complete_for_cache": outcome.complete_for_cache,
            "cache_writes": "none (cache_on=False; checkpoints in temp DB only)",
            "needs_review": document.needs_review,
            "acceptance_status": document.acceptance_status,
            "failed_stage": document.failed_stage,
            "error": str(document.error or "")[:500],
            "validation_errors": [str(e)[:160] for e in (document.validation_errors or [])[:10]],
            "review_issue_kinds": [
                {"category": i.category, "target": i.target, "severity": i.severity}
                for i in (document.review_issues or [])],
            "field_count": len(document.fields or []),
            "table_count": len(document.tables or []),
            "rejected_count": len(document.rejected_candidates or []),
            "usage": {k: {kk: int(vv or 0) for kk, vv in (v or {}).items()
                           if isinstance(vv, (int, float))}
                      for k, v in usage.items()},
            "diagnostics": document.diagnostics,
        }
        report["status"] = ("completed-with-usable-region" if usable
                            else "completed-without-usable-region"
                            if not document.error else "failed-no-regions")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["model_state_after"] = single._ollama_ps(settings.llm_base_url)
    report["memory_after"] = single._meminfo()
    return report


def _region_texts(blocks, page_text: str) -> list:
    """Split offline (no inference) to map region ids to region text."""
    from app.services.regions import split_page

    split = split_page(list(blocks or []), page_text, 1)
    return [{"id": r.id, "text": r.text} for r in split.regions]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Region pilot harness: one provenance-verified page through "
                    "the actual region orchestration (prepared, gated).")
    parser.add_argument("--job-id", default=DEFAULT_JOB)
    parser.add_argument("--db", default=str(REPO_ROOT / "data-local" / "extraction.db"))
    parser.add_argument("--sources", default=str(REPO_ROOT / "data-local" / "sources"))
    parser.add_argument("--out", default="/tmp/diag_region_pilot.json")
    parser.add_argument("--doc-type", default=None,
                        help="explicit doc type (bypasses Router, recorded); "
                             "omit to run Router (counted)")
    parser.add_argument("--region-contracts", dest="region_contracts",
                        action="store_true", default=True,
                        help="prepend kind-specific output contracts (default on)")
    parser.add_argument("--no-region-contracts", dest="region_contracts",
                        action="store_false",
                        help="legacy region task text (byte-identical default-off path)")
    parser.add_argument("--overall-deadline", type=float, default=OVERALL_DEADLINE_S)
    args = parser.parse_args()

    report = await run_pilot(args)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    if report.get("status") in ("completed-with-usable-region",):
        return 0
    return 1 if report.get("status", "").startswith(("completed", "failed")) else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
