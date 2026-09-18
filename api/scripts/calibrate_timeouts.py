#!/usr/bin/env python3
"""Bounded local timeout-calibration benchmark (10 SROIE, ollama-local only).

Reuses run_eval isolation + dispatch ledger + provenance + resource sampling.
Measurement-only experimental bounds (NOT production defaults):
  300s per inference HTTP attempt, 600s per document, 60min whole task,
  60 HTTP dispatches total, concurrency 1, no retries/fallbacks/corrections.

Order: measure -> propose -> confirm in isolation -> apply.
--apply requires --confirm success in the same run and only edits local
timeout keys in api/.env; otherwise the proposal is reported, not applied.

Usage (repo root, venv):
  python api/scripts/calibrate_timeouts.py --out /tmp/calib [--confirm] [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings, validate_timeout_config  # noqa: E402
from app.services import client as client_mod  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    UploadedFilePart,
)
from app.services.field_matching import values_match  # noqa: E402
from app.services.request_control import (  # noqa: E402
    attempt_ceiling_s,
    collect_dispatch_intents,
    collect_dispatches,
)
from app.services.timeout_calibration import (  # noqa: E402
    CLIENT_TIMEOUT_DISCLAIMER,
    ProgressStore,
    apply_local_profile,
    assert_isolated_storage,
    assess_provider_idle,
    atomic_write_json,
    build_partial_report,
    candidate_request_timeout,
    check_task_budget,
    decide_apply,
    derive_stage_proposal,
    describe_dispatch_state,
    effective_attempt_ceiling,
    plan_confirmation,
    stage_censored_counts,
    summarize_durations,
    validate_measurement,
)

SROIE_ORDER = [
    "sroie_X51005301667.jpg",
    "sroie_X51005663293.jpg",
    "sroie_X51005663297.jpg",
    "sroie_X51005663311.jpg",
    "sroie_X51005806685.jpg",
    "sroie_X51006414713.jpg",
    "sroie_X51006556815.jpg",
    "sroie_X51006857265.jpg",
    "sroie_X51008123604.jpg",
    "sroie_X51008142033.jpg",
]
EXPERIMENT_REQUEST_S = 300.0
EXPERIMENT_STAGE_S = 550.0  # single 300s attempt fits; no retry room by design
DOC_LIMIT_S = 600.0
TASK_LIMIT_S = 3600.0
MAX_DISPATCHES = 60
# Production per-generation budget is 4 attempts; calibration patches to a
# single attempt, which is strictly tighter — honored, not raised.
SUPPORTED_FIELDS = ("seller_name", "seller_address", "total_amount")
CONFIRM_DRAIN_S = 30.0  # extended drain after a timeout before re-polling
MIN_CONFIRM_COMPLETED = 5  # sweep sufficiency floor for a defensible proposal


def _meminfo() -> dict:
    out: dict = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, rest = line.partition(":")
            if k.strip() in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                out[k.strip()] = rest.strip()
    except OSError:
        pass
    return out


def _ollama(base_url: str, route: str, timeout: int = 10) -> dict:
    host = base_url.split("/v1")[0]
    try:
        with urllib.request.urlopen(host + route, timeout=timeout) as r:  # noqa: S310
            return {"reachable": True, "data": json.loads(r.read().decode())}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def _active_app_jobs(settings: Settings) -> list:
    """Read-only check of the real History DB for queued/processing jobs."""
    db = Path(settings.database_path)
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT id,status FROM extraction_jobs WHERE status IN ('queued','processing')"
        ).fetchall()
    finally:
        con.close()


def preflight(settings: Settings) -> dict:
    info: dict = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "temperature": settings.llm_temperature,
        "extraction_max_tokens": settings.extraction_max_tokens,
        "compact": settings.extraction_compact_request,
        "regions": settings.region_extraction_enabled,
        "concurrency": settings.llm_max_concurrent_requests,
    }
    if settings.llm_provider != "ollama-local":
        raise RuntimeError(f"refusing non-local provider {settings.llm_provider!r}")
    if settings.region_extraction_enabled:
        raise RuntimeError("regions must stay default-off for calibration")
    # Model is taken from effective configuration (LLM_MODEL); presence is
    # verified via tags only — never pulled or downloaded here.
    if not settings.llm_model:
        raise RuntimeError("refusing empty LLM_MODEL")
    tags = _ollama(settings.llm_base_url, "/api/tags")
    info["tags_reachable"] = tags["reachable"]
    if not tags["reachable"]:
        raise RuntimeError(f"ollama unreachable: {tags.get('error')}")
    names = [m.get("name", "") for m in (tags.get("data") or {}).get("models", [])]
    info["models"] = names
    if not any(settings.llm_model in n or n in settings.llm_model for n in names):
        # model presence without pull: tags only, never /api/pull
        raise RuntimeError(f"model {settings.llm_model!r} not in local tags {names}")
    ps = _ollama(settings.llm_base_url, "/api/ps")
    info["ps"] = ps
    # No active application jobs (read-only check of the real History DB).
    # /api/ps + /api/tags reachability alone never proves provider idleness;
    # the sweep gate additionally requires terminal dispatch outcomes and
    # re-polls after any timeout (see assess_provider_idle).
    db = Path(settings.database_path)
    info["app_db"] = str(db)
    try:
        rows = _active_app_jobs(settings)
    except Exception as exc:  # noqa: BLE001
        info["active_jobs_check"] = f"skipped: {exc}"
    else:
        info["active_jobs"] = rows
        if rows:
            raise RuntimeError(f"active application jobs present: {rows}")
    return info


async def _single_structured_inner(self, *, model, prompt, response_schema,
                                   temperature, max_tokens, disable_reasoning,
                                   reasoning_effort):
    """Single-attempt measurement variant: strongest tier once, no fallback/correction."""
    import time as _t

    from app.services.client import (
        _truncate,
        build_structured_messages,
        compact_request_enabled,
    )
    from app.services.provider_capabilities import resolve_capabilities
    request_summary = {"model": model, "prompt": _truncate(prompt),
                       "response_schema": response_schema.__name__,
                       "temperature": temperature,
                       "disable_reasoning": disable_reasoning,
                       "reasoning_effort": reasoning_effort or None}
    endpoint = getattr(self, "_endpoint", "")
    resolve_capabilities(endpoint=endpoint, model=model,
                         provider_label=str(getattr(self.settings, "llm_provider", "") or ""))
    tier = self._strongest_tier(model) or "plain"
    compact = compact_request_enabled(self.settings)
    messages = build_structured_messages(prompt=prompt, response_schema=response_schema,
                                         compact=compact, tier=tier)
    t0 = _t.perf_counter()
    raw_text, raw_response, pt, ct, tt = await self._call_tier(
        tier, model=model, messages=messages, response_schema=response_schema,
        temperature=temperature, request_summary=request_summary,
        max_tokens=max_tokens, disable_reasoning=disable_reasoning,
        reasoning_effort=reasoning_effort)
    elapsed = _t.perf_counter() - t0
    parsed, diagnosis = self._try_parse_detailed(response_schema, raw_text, raw_response)
    from app.services.client import _extract_finish_reason
    finish = _extract_finish_reason(raw_response)
    if parsed is not None:
        self._record_usage(pt, ct, tt)
        from app.services.client import ClientResult
        return ClientResult(parsed=parsed, raw_text=raw_text, raw_response=raw_response,
                            request_summary=request_summary, prompt_tokens=pt,
                            completion_tokens=ct, total_tokens=tt, diagnosis="ok",
                            finish_reason=finish,
                            reasoning_effort=self._effective_reasoning_after_fallback(
                                model, reasoning_effort),
                            output_mode=tier)
    raise self._classified_error(
        response_schema=response_schema, model=model, tier=tier, raw_text=raw_text,
        raw_response=raw_response, diagnosis=diagnosis, prompt_tokens=pt,
        completion_tokens=ct, total_tokens=tt, elapsed=elapsed,
        max_tokens=max_tokens, request_summary=request_summary, attempts_made=1)


def install_single_attempt():
    client_mod.TIMEOUT_MAX_RETRIES = 0
    client_mod.RATE_LIMIT_MAX_RETRIES = 1
    client_mod.Client._generate_structured_inner = _single_structured_inner  # type: ignore[method-assign]


def score_supported(gold_fields: dict, docs: list) -> dict:
    pred: dict = {}
    if docs:
        first = docs[0]
        fields = first.get("fields", []) if isinstance(first, dict) else []
        for f in fields:
            if isinstance(f, dict) and f.get("name") in SUPPORTED_FIELDS:
                pred[f["name"]] = f.get("value")
    tp = fp = fn = 0
    for k in SUPPORTED_FIELDS:
        exp = gold_fields.get(k)
        got = pred.get(k, None)
        if exp is None:
            continue
        if got is not None and values_match(got, exp):
            tp += 1
        elif got is not None:
            fp += 1
        else:
            fn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec,
            "pred_supported": pred}


def _not_attempted(name: str, reason: str) -> dict:
    """Never-started document record: zeros are valid here (no operation
    occurred) and every stage is explicitly not_run. Paths where work may
    have started (deadline, cancel, exception) override stage seconds to
    null/unknown instead — zero without evidence is never reported."""
    return {"source_id": name, "outcome": "not_attempted",
            "dispatches": 0, "timeouts": 0, "errors": 0,
            "ocr_seconds": 0.0,
            "router_seconds": 0.0, "extractor_seconds": 0.0,
            "judge_seconds": 0.0,
            "stage_timing_status": {"ocr": "not_run", "router": "not_run",
                                    "extractor": "not_run", "judge": "not_run"},
            "attempt_durations": {"router": [], "extractor": [], "judge": []},
            "censored_durations": {"router": [], "extractor": [], "judge": []},
            "attempt_outcomes": {},
            "total_seconds": 0.0,
            "reason": reason}


def _fill_attempt_evidence(rec: dict, events: list) -> dict:
    """Authoritative HTTP-attempt evidence from terminal ledger events.

    Completed (ok) per-stage durations feed statistics via
    ``attempt_durations``. Non-ok terminal durations are censored
    observations: reported under ``censored_durations`` with their measured
    values, never merged into completed samples. Per-outcome sequences stay
    in ``attempt_outcomes`` (per-stage censoring inputs). Nothing is
    synthesized from elapsed time or retry policy — only recorded transport
    outcomes count.
    """
    stages = ("router", "extractor", "judge")
    rec["attempt_durations"] = {
        st: [e.duration_s for e in events
             if e.stage == st and e.outcome == "ok"]
        for st in stages
    }
    rec["censored_durations"] = {
        st: [e.duration_s for e in events
             if e.stage == st and e.outcome != "ok"]
        for st in stages
    }
    rec["attempt_outcomes"] = {
        st: [e.outcome for e in events if e.stage == st]
        for st in {e.stage for e in events}
    }
    rec["timeouts"] = sum(1 for e in events if e.outcome == "timeout")
    rec["errors"] = sum(1 for e in events if e.outcome not in ("ok", "timeout"))
    # Extractor-only prompt-size fingerprint (chars + short hash; never
    # bodies). None when no extractor attempt was recorded.
    rec["extractor_prompt"] = next(
        ({"chars": e.prompt_chars, "sha": e.prompt_sha}
         for e in events
         if e.stage == "extractor" and e.prompt_chars is not None),
        None,
    )
    return rec


def _mark_stage_seconds_unknown(rec: dict) -> dict:
    """Work may have started but no per-stage split durations survived
    (deadline, cancel, raised exception): null/unknown, never fabricated
    zeros. Zero stays valid only for never-started records (_not_attempted)
    and stages provably not run (see the success path below)."""
    for key in ("ocr_seconds", "router_seconds", "extractor_seconds",
                "judge_seconds"):
        rec[key] = None
    rec["stage_timing_status"] = {"ocr": "unknown", "router": "unknown",
                                  "extractor": "unknown", "judge": "unknown"}
    return rec


class DocInterrupted(Exception):
    """Operator cancellation during one document, carrying its partial record.

    CancelledError (BaseException) must never be misreported as a timeout:
    the loop persists record, writes a partial report, and stops the sweep
    without claiming any timeout fired.
    """

    def __init__(self, record: dict) -> None:
        super().__init__(f"interrupted during {record.get('source_id', '?')}")
        self.record = record


async def _run_one_doc(
    *,
    service: DocumentExtractionService,
    settings: Settings,
    gold_dir: Path,
    by_name: dict,
    name: str,
    task_start: float,
    task_deadline: float,
    cold: bool,
    phase: str,
) -> tuple[dict, int, bool]:
    """Run one document; returns (record, known_dispatches, had_timeout_or_cancel).

    Every attempt respects min(request, doc-remaining, task-remaining) via
    the transport ceiling. No retries/fallbacks/corrections (single-attempt
    patch installed by the caller).

    Evidence contract (failed_stage gap fix, 2026-09-16): the record carries
    terminal ledger events on EVERY path — successful return, returned
    failure (failed_stage document), raised exception, and cancellation —
    because inner service collectors propagate to these outer scopes on
    exit. Stage seconds come from the returned document's TOP-LEVEL
    ``timings`` (durations live there per the schema; ``diagnostics`` never
    carried them, which zeroed the smoke run). Each stage reports a timing
    status (measured / skipped / not_run / unknown); unknown stays null —
    zero is reported only when the stage provably did not run. Stage-block
    durations (TimeoutGuard: queue + attempts + backoff) are kept distinct
    from per-HTTP-attempt transport durations; timed-out attempts are
    censored observations (durations reported under censored_durations,
    never merged into the completed samples in attempt_durations).
    Operator CancelledError is never a timeout: it raises DocInterrupted
    carrying the partial record.
    """
    doc_start = time.monotonic()
    task_remaining = max(0.0, task_deadline - doc_start)
    doc_deadline = min(doc_start + DOC_LIMIT_S, task_deadline)
    doc_remaining = max(0.0, doc_deadline - doc_start)
    ceiling = effective_attempt_ceiling(
        request_timeout_s=EXPERIMENT_REQUEST_S,
        doc_remaining_s=doc_remaining,
        task_remaining_s=task_remaining,
    )
    if ceiling <= 0.0:
        return (_not_attempted(name, "no remaining doc/task time for one bounded attempt"),
                0, False)
    data = (gold_dir / name).read_bytes()
    token = attempt_ceiling_s.set(ceiling)

    def _detail(events: list, intents: list, *, inflight_note: str = "") -> dict:
        detail = describe_dispatch_state(events, intents)
        detail["effective_request_s"] = ceiling
        if inflight_note:
            detail["inflight"] = inflight_note
        return detail

    try:
        with collect_dispatches() as events:
            with collect_dispatch_intents() as intents:
                try:
                    t0 = time.monotonic()
                    res = await asyncio.wait_for(
                        service.extract_group(
                            [UploadedFilePart(name,
                                              mimetypes.guess_type(name)[0] or "image/jpeg",
                                              data)],
                            force_refresh=True,
                        ),
                        timeout=max(1.0, doc_deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    # Experimental document deadline fired. Terminal events
                    # captured so far are KEPT (never discarded); any
                    # unsettled intent is explicitly uncertain — the server
                    # may still be generating (see CLIENT_TIMEOUT_DISCLAIMER).
                    total_s = time.monotonic() - doc_start
                    rec = _not_attempted(name, "experimental document deadline 600s")
                    rec["outcome"] = "cancelled"
                    rec["total_seconds"] = total_s
                    rec["phase"] = phase
                    rec["effective_request_s"] = ceiling
                    rec["dispatch_detail"] = _detail(
                        list(events), list(intents),
                        inflight_note=CLIENT_TIMEOUT_DISCLAIMER)
                    rec["dispatches"] = len(events)
                    _fill_attempt_evidence(rec, list(events))
                    _mark_stage_seconds_unknown(rec)
                    return (rec, len(events), True)
                except asyncio.CancelledError:
                    # Operator/shutdown cancel — not a timeout, never
                    # reported as one. Carry the partial record outward.
                    total_s = time.monotonic() - doc_start
                    rec = _not_attempted(name, "operator cancel during document")
                    rec["outcome"] = "interrupted"
                    rec["total_seconds"] = total_s
                    rec["phase"] = phase
                    rec["effective_request_s"] = ceiling
                    rec["dispatch_detail"] = _detail(
                        list(events), list(intents),
                        inflight_note="cancelled mid-document; unsettled "
                                      "intents are explicitly uncertain")
                    rec["dispatches"] = len(events)
                    _fill_attempt_evidence(rec, list(events))
                    _mark_stage_seconds_unknown(rec)
                    raise DocInterrupted(rec)
                except Exception as exc:  # noqa: BLE001
                    total_s = time.monotonic() - doc_start
                    rec = _not_attempted(
                        name, f"{type(exc).__name__}: {str(exc)[:300]}")
                    rec["outcome"] = "failed"
                    rec["total_seconds"] = total_s
                    rec["phase"] = phase
                    rec["effective_request_s"] = ceiling
                    rec["dispatch_detail"] = _detail(list(events), list(intents))
                    rec["dispatches"] = len(events)
                    _fill_attempt_evidence(rec, list(events))
                    _mark_stage_seconds_unknown(rec)
                    return (rec, len(events), False)
                total_s = time.monotonic() - t0
                docs = res.model_dump(mode="json").get("documents", [])
                # Stage durations live on the document's TOP-LEVEL timings
                # (schema: durations in timings, counts in usage). The old
                # code read diagnostics.timings — a key _page_diagnostics
                # never emits — which zeroed every stage of the smoke run.
                doc0_raw = docs[0] if docs else {}
                doc0 = doc0_raw if isinstance(doc0_raw, dict) else {}
                try:
                    top_timings = dict(doc0.get("timings", {}) or {})
                except Exception:  # noqa: BLE001
                    top_timings = {}
                failed_raw = doc0.get("failed_stage")
                failed = failed_raw if isinstance(failed_raw, str) else ""
                # Pipeline order: stages after the failure point never ran
                # (zero valid); the failure point and earlier stages ran
                # (measured when present, unknown when not).
                fail_idx = {"ocr": -1, "router": 0, "extractor": 1,
                            "validator": 2}.get(failed, 3)
                judge_state = doc0.get("judge_status", "unknown")
                stage_seconds: dict[str, float | None] = {}
                stage_status: dict[str, str] = {}
                for idx, st in enumerate(("router", "extractor", "judge")):
                    if st == "judge" and judge_state == "skipped":
                        # Skipped by design (clean page): no dispatch, so
                        # zero is the honest value, flagged as skipped.
                        stage_seconds[st] = 0.0
                        stage_status[st] = "skipped"
                    elif idx > fail_idx:
                        stage_seconds[st] = 0.0
                        stage_status[st] = "not_run"
                    elif st in top_timings:
                        try:
                            stage_seconds[st] = float(top_timings[st])
                        except (TypeError, ValueError):
                            stage_seconds[st] = None
                        stage_status[st] = ("measured"
                                            if stage_seconds[st] is not None
                                            else "unknown")
                    else:
                        stage_seconds[st] = None
                        stage_status[st] = "unknown"
                if "ocr" in top_timings:
                    try:
                        ocr_seconds: float | None = float(top_timings["ocr"])
                    except (TypeError, ValueError):
                        ocr_seconds = None
                    ocr_status = ("measured" if ocr_seconds is not None
                                  else "unknown")
                else:
                    ocr_seconds, ocr_status = None, "unknown"
                outcome = "failed"
                validation_errors: list = []
                judge_status = "unknown"
                evidence: dict = {}
                if docs:
                    d0 = docs[0]
                    validation_errors = d0.get("validation_errors", []) or []
                    judge_status = d0.get("judge_status", "unknown")
                    fields = d0.get("fields", []) or []
                    evidence = {"n_fields": len(fields),
                                "missing_source": sum(1 for f in fields
                                                      if not (f.get("source_span") or ""))}
                    outcome = "completed" if not d0.get("failed_stage") else "partial"
                rec = {
                    "source_id": name, "outcome": outcome, "phase": phase,
                    "dispatches": len(events),
                    "ocr_seconds": ocr_seconds,
                    "ocr_cache": "isolated-empty-miss",
                    "router_seconds": stage_seconds["router"],
                    "extractor_seconds": stage_seconds["extractor"],
                    "judge_seconds": stage_seconds["judge"],
                    "stage_timing_status": {"ocr": ocr_status, **stage_status},
                    "timing_note": ("stage seconds are TimeoutGuard stage-block "
                                    "durations (queue + attempts + backoff); "
                                    "attempt/censored durations are per-HTTP-"
                                    "dispatch transport timings"),
                    "total_seconds": total_s,
                    "effective_request_s": ceiling,
                    "dispatch_detail": _detail(list(events), list(intents)),
                    "cold": cold,
                    "mem": _meminfo(),
                    "validation_errors": validation_errors,
                    "judge_status": judge_status,
                    "evidence": evidence,
                    "error": None,
                }
                _fill_attempt_evidence(rec, list(events))
                # post-extraction scoring only (gold never an input)
                gold_fields = (by_name[name]["pages"][0].get("fields", {}) or {})
                rec["accuracy"] = score_supported(gold_fields, docs)
                rec["unsupported_date_present"] = "sroie_receipt_date" in gold_fields
                validate_measurement({k: rec[k] for k in
                                      ("source_id", "outcome", "dispatches",
                                       "ocr_seconds", "router_seconds",
                                       "extractor_seconds", "judge_seconds",
                                       "total_seconds")})
                return (rec, len(events), rec["timeouts"] > 0)
    finally:
        attempt_ceiling_s.reset(token)


async def _ollama_async(base_url: str, route: str, timeout: int = 10) -> dict:
    """Non-blocking provider probe: blocking urllib isolated in a thread.

    Synchronous blocking work defeats asyncio deadline delivery (a
    Task.cancel / wait_for timeout cannot run until the loop unblocks), so
    probes run via to_thread with their own outer bound. Cancellation
    during the probe reports unreachable instead of hanging the gate.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ollama, base_url, route, timeout),
            timeout=float(timeout) + 5.0,
        )
    # Note: operator CancelledError (BaseException) intentionally bypasses
    # this handler so task cancellation aborts the gate promptly.
    except Exception as exc:  # noqa: BLE001 — incl. wait_for TimeoutError
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


async def _idle_gate(
    *,
    settings: Settings,
    had_timeout_or_cancel: bool,
    task_deadline: float,
) -> tuple[str, str]:
    """Provider-idleness gate before the next document (deadline-deliverable).

    /api/ps reachability alone never proves idleness; the verdict combines
    terminal dispatch outcomes, application-job state, and provider
    reachability. After a timeout/cancel, server-side cancellation is
    unprovable from the client: extended drain + re-poll, proceed only
    provisionally (recorded), and a second consecutive deadline stops the
    sweep via the caller's consecutive counter.

    Fully async: probes run off-loop and the drain uses asyncio.sleep, so
    document/task deadlines and operator cancellation are delivered
    promptly even mid-gate (the old blocking time.sleep/urllib could hold
    the loop for tens of seconds past a deadline).
    """
    ps_before = await _ollama_async(settings.llm_base_url, "/api/ps")
    try:
        jobs = _active_app_jobs(settings)
    except Exception as exc:  # noqa: BLE001
        return ("stop", f"application job state unreadable: {exc}")
    decision, reason = assess_provider_idle(
        active_jobs=jobs,
        had_timeout_or_cancel=had_timeout_or_cancel,
        ps_reachable_before=ps_before["reachable"],
        ps_reachable_after=ps_before["reachable"],
    )
    if decision == "proceed-provisional":
        drain = min(CONFIRM_DRAIN_S, max(0.0, task_deadline - time.monotonic()))
        if drain > 0:
            await asyncio.sleep(drain)
        try:
            jobs_after = _active_app_jobs(settings)
        except Exception as exc:  # noqa: BLE001
            return ("stop", f"post-drain job state unreadable: {exc}")
        ps_after = await _ollama_async(settings.llm_base_url, "/api/ps")
        decision2, reason2 = assess_provider_idle(
            active_jobs=jobs_after,
            had_timeout_or_cancel=True,  # still unprovable: stays provisional
            ps_reachable_before=ps_before["reachable"],
            ps_reachable_after=ps_after["reachable"],
        )
        if decision2 == "stop":
            return (decision2, f"post-drain re-poll failed: {reason2}")
        return ("proceed-provisional",
                f"{reason}; drained {drain:.0f}s and re-polled before proceeding")
    return (decision, reason)


def _summarize(records: list[dict]) -> tuple[dict, dict]:
    """Per-stage summaries from each stage's own transport durations only.

    Completed samples = outcome-ok ledger attempts (queue wait excluded, so
    the request timeout never absorbs unrelated queue time). Censoring is
    per-stage, never lumped: Router/Judge proposals never inherit Extractor
    timeouts. Judge-skipped docs (JUDGE_SKIP_WHEN_CLEAN) contribute no
    judge sample — recorded separately, not as 0s measurements.
    """
    stages = ("router", "extractor", "judge")
    completed = [r for r in records if r["outcome"] in ("completed", "partial")]
    # Per-stage censored counts from recorded per-attempt outcomes.
    # Cross-stage lumping never happens: each stage's summary uses only
    # its own samples plus its own censored count.
    pairs = [(st, outcome) for r in completed
             for st, outcomes in ((r.get("attempt_outcomes", {}) or {}).items())
             for outcome in outcomes]
    raw_censored = stage_censored_counts(pairs)
    censored = {st: int(raw_censored.get(st, 0)) for st in stages}
    summary = {}
    for st in stages:
        samples = [d for r in completed
                   for d in ((r.get("attempt_durations", {}) or {}).get(st, []) or [])]
        summary[st] = summarize_durations(samples, censored_count=censored[st],
                                          label=st)
    n_judge_skipped = sum(1 for r in completed
                          if r.get("judge_status") in ("skipped", "unavailable")
                          and not ((r.get("attempt_durations", {}) or {})
                                   .get("judge")))
    summary["judge"]["n_skipped"] = n_judge_skipped
    candidates = {k: candidate_request_timeout(v.get("p95")) for k, v in summary.items()}
    return summary, candidates


def _derive_proposals(summary: dict, prod: dict) -> dict:
    """Stage-specific proposals; Router/Judge never inflated from Extractor."""
    stage_limits = {"router": prod["router_timeout_s"],
                    "extractor": prod["extractor_timeout_s"],
                    "judge": prod["judge_timeout_s"]}
    proposals = {}
    for st in ("router", "extractor", "judge"):
        s = summary[st]
        proposals[st] = derive_stage_proposal(
            p95_s=s.get("p95"), censored_count=int(s.get("n_censored", 0)),
            label=st, current_request_s=prod["request_timeout_s"],
            current_stage_s=stage_limits[st])
    return proposals


def _apply_to_env(proposed: dict[str, float], provider: str) -> dict:
    """Edit api/.env local timeout keys only; secret-safe rollback via report.

    Returns {"old": {...}, "new": {...}}. Refuses non-local providers,
    unverified keys, and cloud-preserved keys via apply_local_profile.
    Rollback re-applies "old" (recorded in the calibration report).
    """
    if provider != "ollama-local":
        raise RuntimeError(f"refusing apply for non-local provider {provider!r}")
    current = {k: proposed[k] for k in proposed}  # validated below
    apply_local_profile(current, proposed, provider=provider)
    env_path = API_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    old: dict[str, float] = {}
    found = set()
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in proposed:
                try:
                    old[key] = float(stripped.split("=", 1)[1].strip() or "nan")
                except ValueError:
                    old[key] = float("nan")
                out_lines.append(f"{key}={proposed[key]:g}\n")
                found.add(key)
                continue
        out_lines.append(line)
    missing = set(proposed) - found
    if missing:
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        for key in sorted(missing):
            out_lines.append(f"{key}={proposed[key]:g}\n")
    env_path.write_text("".join(out_lines), encoding="utf-8")
    return {"old": old, "new": dict(proposed)}


async def main_async(args) -> int:
    out = Path(args.out)
    assert_isolated_storage(out)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    pre = preflight(settings)
    prod = {
        "request_timeout_s": float(settings.llm_request_timeout_seconds),
        "router_timeout_s": float(settings.router_timeout_seconds),
        "extractor_timeout_s": float(settings.extractor_timeout_seconds),
        "judge_timeout_s": float(settings.judge_timeout_seconds),
        "ocr_timeout_s": float(settings.ocr_timeout_seconds),
    }
    # Freeze experimental (process-local only) bounds
    settings.llm_request_timeout_seconds = EXPERIMENT_REQUEST_S
    settings.router_timeout_seconds = EXPERIMENT_STAGE_S
    settings.extractor_timeout_seconds = EXPERIMENT_STAGE_S
    settings.judge_timeout_seconds = EXPERIMENT_STAGE_S
    settings.result_cache_enabled = False
    settings.database_enabled = False
    settings.audit_log_enabled = False
    settings.temporal_enabled = False
    settings.region_extraction_enabled = False
    install_single_attempt()
    try:
        startup_warnings = validate_timeout_config(settings)
    except Exception:  # noqa: BLE001
        startup_warnings = []
    # Expected: experimental bounds intentionally leave no retry room.

    gold_dir = REPO_ROOT / "api/app/data/knowledge_base/ground_truth"
    manifest = json.loads((gold_dir / "manifest.json").read_text())
    by_name = {f["filename"]: f for f in manifest["files"]}
    for name in SROIE_ORDER:
        if name not in by_name:
            raise RuntimeError(f"SROIE source missing from manifest: {name}")

    tmp = Path(tempfile.mkdtemp(prefix="calib-", dir="/tmp"))
    try:
        kb = tmp / "kb"
        shutil.copytree(gold_dir.parent / "field_catalog", kb / "field_catalog")
        settings.knowledge_base_path = str(kb)
        settings.ocr_cache_path = str(tmp / "ocr-results")  # isolated empty
        Path(settings.ocr_cache_path).mkdir(parents=True, exist_ok=True)
        settings.source_storage_path = str(tmp / "sources")
        settings.cache_path = str(tmp / "cache")
        Path(settings.cache_path).mkdir(parents=True, exist_ok=True)
        service = DocumentExtractionService(settings)
        # Document order: full SROIE sweep by default; --only restricts to
        # one document (durable-measurement smoke scope). Unknown ids fail
        # fast instead of silently narrowing the run.
        order = list(SROIE_ORDER)
        if getattr(args, "only", None):
            if args.only not in SROIE_ORDER:
                raise RuntimeError(f"--only {args.only!r} not in SROIE_ORDER")
            order = [args.only]
        # Durable incremental progress: every doc-start intent and every
        # terminal per-doc outcome is flushed to the isolated out dir as
        # soon as it happens, so a killed run always leaves an honest
        # partial trail (the 2026-09-16 lost-progress failure wrote only
        # at end-of-run and persisted zero records).
        store = ProgressStore(out)
        task_start = time.monotonic()
        task_deadline = task_start + TASK_LIMIT_S
        records: list[dict] = []
        total_dispatches = 0
        total_uncertain = 0
        consec_deadlines = 0
        cold_done = False
        idle_notes: list[str] = []
        had_timeout_or_cancel = False

        def _keep(rec: dict) -> None:
            """Append to the in-memory run AND persist immediately."""
            nonlocal total_dispatches, total_uncertain
            records.append(rec)
            store.record_outcome(rec)
            try:
                detail = rec.get("dispatch_detail") or {}
                total_uncertain += int(detail.get("dispatches_uncertain", 0) or 0)
            except Exception:  # noqa: BLE001 — accounting only, never fatal
                pass

        def _partial(deadline_fired: str, stop_reason: str) -> dict:
            return build_partial_report(
                records=records, order=order,
                in_progress=(dict(store.in_progress)
                             if store.in_progress else None),
                dispatches_used_known=total_dispatches,
                dispatches_uncertain=total_uncertain,
                elapsed_s=time.monotonic() - task_start,
                deadline_fired=deadline_fired,
                stop_reason=stop_reason,
                had_timeout_or_cancel=had_timeout_or_cancel,
            )

        try:
            for pos, name in enumerate(order):
                stop = check_task_budget(dispatches_used=total_dispatches,
                                         elapsed_s=time.monotonic() - task_start)
                if stop:
                    _keep(_not_attempted(name, stop))
                    continue
                # Dispatch intent: persisted BEFORE transport invocation.
                store.record_start(name, {"phase": "sweep", "index": pos,
                                          "n_planned": len(order),
                                          "cold": not cold_done})
                try:
                    rec, used, had_timeout = await _run_one_doc(
                        service=service, settings=settings, gold_dir=gold_dir,
                        by_name=by_name, name=name, task_start=task_start,
                        task_deadline=task_deadline, cold=not cold_done,
                        phase="sweep")
                except DocInterrupted as di:
                    # Operator cancel mid-document: persist the interrupted
                    # record, write a partial report, stop — never a timeout.
                    had_timeout_or_cancel = True
                    _keep(di.record)
                    store.write_partial_report(_partial(
                        "operator-cancel (asyncio.CancelledError), not a timeout",
                        f"operator cancel during {name}; sweep stopped; "
                        f"persisted records stand"))
                    return 3
                total_dispatches += used
                _keep(rec)
                cold_done = True
                had_timeout_or_cancel = had_timeout_or_cancel or had_timeout
                if had_timeout:
                    consec_deadlines += 1
                elif rec["outcome"] in ("failed", "partial"):
                    consec_deadlines = 0
                else:
                    consec_deadlines = 0
                if consec_deadlines >= 2:
                    idx = order.index(name)
                    for rest in order[idx + 1:]:
                        _keep(_not_attempted(
                            rest, "two consecutive experimental deadlines"))
                    break
                # Idleness gate before the next document (ps alone never proves it).
                if name != order[-1]:
                    gate, note = await _idle_gate(
                        settings=settings,
                        had_timeout_or_cancel=had_timeout,
                        task_deadline=task_deadline)
                    rec["idle_before_next"] = gate
                    idle_notes.append(f"{name}: {gate} — {note}")
                    if gate == "stop":
                        idx = order.index(name)
                        for rest in order[idx + 1:]:
                            _keep(_not_attempted(
                                rest, f"idle gate stopped sweep: {note}"))
                        break
        except asyncio.CancelledError:
            # Shutdown between documents: persist + partial, never a timeout.
            had_timeout_or_cancel = True
            store.write_partial_report(_partial(
                "operator-cancel (asyncio.CancelledError), not a timeout",
                "operator cancel between documents; sweep stopped; "
                "persisted records stand"))
            raise
        # Normal reporting is guarded: if summary/confirm/report
        # generation itself raises, the already-persisted per-doc records
        # still stand and a partial report is written from them (exit 2).
        try:
            summary, candidates = _summarize(records)
            proposals = _derive_proposals(summary, prod)
            n_completed = sum(1 for r in records if r.get("outcome") == "completed")
            sufficient = (
                n_completed >= MIN_CONFIRM_COMPLETED
                and not proposals["extractor"]["withheld"]
            )
            confirm_records: list[dict] = []
            confirm_ids: list[str] = []
            confirm_blocked = ""
            applied: dict = {"applied": False, "reason": "not requested"}
            if args.confirm or args.apply:
                confirm_ids = plan_confirmation(
                    records, order, dispatches_used=total_dispatches,
                    elapsed_s=time.monotonic() - task_start,
                    max_dispatches=MAX_DISPATCHES, max_seconds=TASK_LIMIT_S)
                if not sufficient:
                    confirm_ids = []
                    confirm_blocked = (
                        "sweep insufficient for a defensible proposal "
                        f"(completed={n_completed}, extractor p95 "
                        f"{'withheld' if proposals['extractor']['withheld'] else 'available'})"
                    )
                elif not confirm_ids:
                    confirm_blocked = "shared task dispatch/time budget exhausted"
                else:
                    confirm_blocked = ""
                if confirm_ids:
                    # Confirmation runs the proposed profile in an isolated
                    # service (fresh tmp KB/OCR/result-cache-off); never a
                    # service restart. Shares the sweep's dispatch/time budget.
                    proposed_request = float(
                        proposals["extractor"]["candidate_request_s"])
                    confirm_settings = Settings()
                    confirm_settings.llm_request_timeout_seconds = proposed_request
                    confirm_settings.router_timeout_seconds = prod["router_timeout_s"]
                    confirm_settings.extractor_timeout_seconds = prod["extractor_timeout_s"]
                    confirm_settings.judge_timeout_seconds = prod["judge_timeout_s"]
                    confirm_settings.result_cache_enabled = False
                    confirm_settings.database_enabled = False
                    confirm_settings.audit_log_enabled = False
                    confirm_settings.temporal_enabled = False
                    confirm_settings.region_extraction_enabled = False
                    confirm_tmp = Path(tempfile.mkdtemp(prefix="calib-confirm-", dir="/tmp"))
                    try:
                        ckb = confirm_tmp / "kb"
                        shutil.copytree(gold_dir.parent / "field_catalog",
                                        ckb / "field_catalog")
                        confirm_settings.knowledge_base_path = str(ckb)
                        confirm_settings.ocr_cache_path = str(confirm_tmp / "ocr-results")
                        Path(confirm_settings.ocr_cache_path).mkdir(
                            parents=True, exist_ok=True)
                        confirm_settings.source_storage_path = str(
                            confirm_tmp / "sources")
                        confirm_settings.cache_path = str(confirm_tmp / "cache")
                        Path(confirm_settings.cache_path).mkdir(
                            parents=True, exist_ok=True)
                        confirm_service = DocumentExtractionService(confirm_settings)
                        for cname in confirm_ids:
                            stop = check_task_budget(
                                dispatches_used=total_dispatches,
                                elapsed_s=time.monotonic() - task_start,
                                max_dispatches=MAX_DISPATCHES,
                                max_seconds=TASK_LIMIT_S)
                            if stop:
                                c_rec = _not_attempted(cname, stop)
                                c_rec["phase"] = "confirm"
                                confirm_records.append(c_rec)
                                store.record_outcome(c_rec)
                                continue
                            store.record_start(cname, {"phase": "confirm",
                                                       "proposed_request_s":
                                                           proposed_request})
                            try:
                                rec, used, had_timeout = await _run_one_doc(
                                    service=confirm_service, settings=confirm_settings,
                                    gold_dir=gold_dir, by_name=by_name, name=cname,
                                    task_start=task_start, task_deadline=task_deadline,
                                    cold=False, phase="confirm")
                            except DocInterrupted as di:
                                had_timeout_or_cancel = True
                                confirm_records.append(di.record)
                                store.record_outcome(di.record)
                                store.write_partial_report(_partial(
                                    "operator-cancel (asyncio.CancelledError), not a timeout",
                                    f"operator cancel during confirmation of {cname}; "
                                    f"persisted records stand"))
                                return 3
                            total_dispatches += used
                            try:
                                detail = rec.get("dispatch_detail") or {}
                                total_uncertain += int(
                                    detail.get("dispatches_uncertain", 0) or 0)
                            except Exception:  # noqa: BLE001
                                pass
                            had_timeout_or_cancel = (had_timeout_or_cancel
                                                     or had_timeout)
                            rec["proposed_request_s"] = proposed_request
                            confirm_records.append(rec)
                            store.record_outcome(rec)
                            if had_timeout or rec["outcome"] != "completed":
                                break
                    finally:
                        shutil.rmtree(confirm_tmp, ignore_errors=True)
                else:
                    confirm_records = []
            confirm_ok = bool(confirm_ids) and bool(confirm_records) and all(
                r.get("outcome") == "completed" for r in confirm_records)
            if args.apply:
                if not args.confirm:
                    applied = {"applied": False,
                               "reason": "proposal reported, not applied — "
                                         "confirmation required first (--confirm)"}
                else:
                    fits = all(proposals[st]["retry_fit"].startswith("fits")
                               for st in ("router", "extractor", "judge")
                               if not proposals[st]["withheld"])
                    gate = decide_apply(
                        requested=True, confirmed_in_same_run=confirm_ok,
                        sufficient=sufficient, retry_fit_ok=fits,
                        n_completed=n_completed)
                    if gate.get("apply_approved"):
                        proposed = {"LLM_REQUEST_TIMEOUT_SECONDS": float(
                            proposals["extractor"]["candidate_request_s"])}
                        applied = {"applied": True,
                                   **_apply_to_env(proposed, settings.llm_provider)}
                    else:
                        applied = {"applied": False, "reason": gate["reason"]}
            try:
                git_rev = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
            except Exception:  # noqa: BLE001 — rev informational only
                git_rev = "unknown"
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "preflight": {k: v for k, v in pre.items() if k != "ps"},
                "production_timeouts": prod,
                "startup_warnings_experimental": startup_warnings,
                "conditions": {
                    "model": settings.llm_model, "endpoint": settings.llm_base_url,
                    "temperature": settings.llm_temperature,
                    "max_tokens": settings.extraction_max_tokens,
                    "compact": settings.extraction_compact_request,
                    "regions": False, "retries": 0, "fallbacks": 0, "corrections": 0,
                    "request_timeout": EXPERIMENT_REQUEST_S, "doc_limit": DOC_LIMIT_S,
                    "task_limit": TASK_LIMIT_S, "max_dispatches": MAX_DISPATCHES,
                    "concurrency": 1, "order": order,
                    "ocr_cache": "isolated empty per phase; sweep sources "
                                 "processed once (cold misses by construction)",
                    "judge": "skipped docs contribute no judge sample "
                             "(recorded as n_skipped, never 0s measurements)",
                },
                "records": records,
                "summary": summary,
                "candidates_p95x1.25": candidates,
                "proposals_stage_specific": proposals,
                "confirm_ids": confirm_ids,
                "confirm_blocked": confirm_blocked if (args.confirm or args.apply) else "",
                "confirm_records": confirm_records,
                "confirm_ok": confirm_ok,
                "applied": applied,
                "total_dispatches": total_dispatches,
                "total_dispatches_uncertain": total_uncertain,
                "server_cancellation": ("unknown — " + CLIENT_TIMEOUT_DISCLAIMER
                                        if had_timeout_or_cancel
                                        else "no timeout/cancel observed"),
                "task_elapsed_s": time.monotonic() - task_start,
                "idle_notes": idle_notes,
                "store_write_errors": list(store.write_errors),
                "git_rev": git_rev,
            }
            # Final flushes are clobber-safe: an empty late state never
            # overwrites previously collected evidence.
            store.flush_measurements()
            if records or confirm_records:
                atomic_write_json(out / "calibration_report.json", report)
            else:
                store.write_partial_report(_partial(
                    check_task_budget(dispatches_used=total_dispatches,
                                      elapsed_s=time.monotonic() - task_start)
                    or "run ended with zero terminal records",
                    "zero terminal records; no proposal derived; "
                    "production timeouts unchanged"))
            print(json.dumps({"total_dispatches": total_dispatches,
                              "total_dispatches_uncertain": total_uncertain,
                              "outcomes": {o: sum(1 for r in records if r["outcome"] == o)
                                           for o in ("completed", "partial", "failed",
                                                     "cancelled", "not_attempted",
                                                     "interrupted")},
                              "candidates": candidates,
                              "confirm": confirm_ids,
                              "applied": applied}, indent=2))
            return 0
        except asyncio.CancelledError:
            had_timeout_or_cancel = True
            store.write_partial_report(_partial(
                "operator-cancel (asyncio.CancelledError), not a timeout",
                "operator cancel during reporting; per-doc persisted "
                "records stand"))
            raise
        except Exception as exc:  # noqa: BLE001 — reporting failed; partials stand
            store.note(f"report generation failed: {type(exc).__name__}: {exc}")
            store.write_partial_report(_partial(
                "report-generation-failed (not a measurement outcome)",
                f"normal report generation raised {type(exc).__name__}; "
                f"per-doc persisted records stand; production timeouts unchanged"))
            print(json.dumps({"error": f"report generation failed: {exc!r}",
                              "partial_report": "partial_report.json",
                              "outcomes": {o: sum(1 for r in records if r["outcome"] == o)
                                           for o in ("completed", "partial", "failed",
                                                     "cancelled", "not_attempted",
                                                     "interrupted")}}, indent=2))
            return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None,
                    help="restrict to one SROIE source id (durable-measurement "
                         "smoke scope; unknown ids fail fast)")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
