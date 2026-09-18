"""Timeout-calibration helpers: censored stats, budgets, isolation, config separation.

Offline-only (mocked providers, temp storage). No live inference, no runtime
data/ DBs, no gold answers as prompt/OCR input.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

# --- Timing units and deadline attribution ---

def stage_attempt_timeout_ok(attempt_s: float, request_timeout_s: float) -> bool:
    """True when a single HTTP attempt fits inside its request deadline."""
    return attempt_s <= request_timeout_s


def attribute_deadline(
    *,
    queue_wait_s: float,
    attempt_s: float,
    request_timeout_s: float,
    stage_elapsed_s: float,
    stage_limit_s: float,
) -> str:
    """Attribute a slow stage: queue vs request vs stage (never mixed)."""
    if queue_wait_s < 0 or attempt_s < 0 or stage_elapsed_s < 0:
        raise ValueError("durations must be non-negative")
    if attempt_s > request_timeout_s:
        return "request"
    if stage_elapsed_s > stage_limit_s:
        return "stage"
    if queue_wait_s > 0 and stage_elapsed_s > 0 and queue_wait_s > stage_elapsed_s * 0.5:
        return "queue"
    return "ok"


# --- Whole-task HTTP/time caps ---

def check_task_budget(
    *,
    dispatches_used: int,
    elapsed_s: float,
    max_dispatches: int = 60,
    max_seconds: float = 3600.0,
) -> str | None:
    """None when budget remains; else the binding stop reason."""
    if dispatches_used >= max_dispatches:
        return f"http-cap: {dispatches_used}/{max_dispatches} dispatches"
    if elapsed_s >= max_seconds:
        return f"time-cap: {elapsed_s:.1f}s/{max_seconds:.0f}s"
    return None


def remaining_for_document(
    *, doc_limit_s: float, task_remaining_s: float, request_timeout_s: float
) -> float:
    """Effective per-document bound: min(doc, task) and never above request issues.

    Every attempt must respect remaining document/task time: the effective
    attempt ceiling is min(request_timeout, doc_remaining, task_remaining).
    """
    return max(0.0, min(doc_limit_s, task_remaining_s, request_timeout_s))


def effective_attempt_ceiling(
    *, request_timeout_s: float, doc_remaining_s: float, task_remaining_s: float
) -> float:
    """Per-attempt ceiling: min(request, doc-remaining, task-remaining).

    Every inference attempt must respect remaining document/task time, not
    just the nominal request timeout. Callers apply this to the transport
    timeout before dispatch; a zero ceiling means "do not dispatch".
    """
    return max(0.0, min(request_timeout_s, doc_remaining_s, task_remaining_s))


def effective_transport_timeout(
    base_timeout_s: float, ceiling_s: float | None
) -> float:
    """Transport timeout actually enforced for one HTTP attempt.

    Default-off: None ceiling returns the configured timeout unchanged, so
    all existing paths are byte-identical unless the calibration harness
    sets a per-document ceiling.
    """
    if ceiling_s is None:
        return base_timeout_s
    return max(0.0, min(base_timeout_s, ceiling_s))


def plan_confirmation(
    records: list[dict[str, Any]],
    order: list[str],
    *,
    dispatches_used: int,
    elapsed_s: float,
    max_dispatches: int = 60,
    max_seconds: float = 3600.0,
) -> list[str]:
    """Confirmation doc IDs sharing the sweep's task budget (at most 2).

    Returns [] when the shared dispatch/time budget is exhausted or fewer
    than 2 docs completed — confirmation never exceeds the task caps and
    never runs without a sufficient sweep.
    """
    if check_task_budget(dispatches_used=dispatches_used, elapsed_s=elapsed_s,
                         max_dispatches=max_dispatches,
                         max_seconds=max_seconds) is not None:
        return []
    picked = select_confirmation_docs(records, order)
    if picked is None:
        return []
    return [picked["slowest"], picked["comparison"]]


def stage_censored_counts(events: list[tuple[str, str]]) -> dict[str, int]:
    """Per-stage timeout counts from (stage, outcome) pairs.

    Censoring is never lumped across stages: a Router proposal must not be
    poisoned by Extractor timeouts and vice versa.
    """
    counts: dict[str, int] = {}
    for stage, outcome in events:
        counts.setdefault(stage, 0)
        if outcome == "timeout":
            counts[stage] += 1
    return counts


def select_confirmation_docs(
    records: list[dict[str, Any]], order: list[str]
) -> dict[str, str] | None:
    """Pick at most 2 confirmation docs: slowest completed + predetermined comparison.

    The comparison doc is predetermined (first completed in the recorded
    deterministic order, distinct from the slowest) — never cherry-picked
    post hoc. Returns None when fewer than 2 docs completed.
    """
    completed = [r for r in records if r.get("outcome") == "completed"]
    if len(completed) < 2:
        return None
    slowest = max(completed, key=lambda r: float(r.get("total_seconds", 0.0)))
    by_id = {r["source_id"]: r for r in completed}
    comparison = next(
        (sid for sid in order if sid in by_id and sid != slowest["source_id"]), None
    )
    if comparison is None:
        return None
    return {"slowest": str(slowest["source_id"]), "comparison": comparison}


def derive_stage_proposal(
    *,
    p95_s: float | None,
    censored_count: int,
    label: str,
    current_request_s: float,
    current_stage_s: float,
    factor: float = 1.25,
) -> dict[str, Any]:
    """Stage-specific timeout proposal from that stage's own measurements only.

    Never inflate one stage (e.g. Router/Judge) from another stage's
    (e.g. Extractor) timings: pass each stage's own p95/censoring here.
    Production keeps a same-tier timeout retry (~2x request + 2s backoff
    inside the stage limit); a no-retry benchmark measurement does NOT
    validate retry timing — retry_fit reports whether the candidate
    preserves room for the existing retry inside the current stage limit.
    """
    candidate = candidate_request_timeout(p95_s, factor) if p95_s is not None else None
    withheld = candidate is None
    if withheld:
        retry_fit = "unknown (p95 withheld — no defensible candidate)"
    elif 2 * candidate + 2.0 <= current_stage_s:
        retry_fit = "fits: one same-tier timeout retry fits in current stage limit"
    else:
        retry_fit = (
            "exceeds: candidate leaves no room for the existing timeout retry "
            "in the current stage limit; adopting it would change retry behavior"
        )
    return {
        "label": label,
        "candidate_request_s": candidate,
        "withheld": withheld,
        "current_request_s": current_request_s,
        "current_stage_s": current_stage_s,
        "retry_fit": retry_fit,
        "note": (
            "No-retry benchmark measurements do not by themselves validate "
            "production retry timing; candidate is a starting heuristic (P95 x 1.25)."
        ),
    }


def assess_provider_idle(
    *,
    active_jobs: list,
    had_timeout_or_cancel: bool,
    ps_reachable_before: bool,
    ps_reachable_after: bool,
) -> tuple[str, str]:
    """Decide whether the provider is provably free for the next document.

    /api/ps and /api/tags reachability NEVER prove idleness on their own —
    only the combination of terminal dispatch outcomes (all attempts
    ok/error, none still in flight), no active application jobs, and a
    reachable provider does. After any timeout/cancel, server-side
    cancellation is unprovable from the client, so the best achievable
    verdict is "proceed-provisional" (extended drain + re-poll required;
    the next doc's first terminal outcome confirms retrospectively, and a
    second consecutive deadline stops the sweep).
    """
    if active_jobs:
        return ("stop", f"active application jobs present: {active_jobs}")
    if not (ps_reachable_before and ps_reachable_after):
        return ("stop", "provider unreachable around the document; state unknown")
    if had_timeout_or_cancel:
        return (
            "proceed-provisional",
            "timeout/cancel occurred; server-side cancellation unprovable from "
            "/api/ps — extended drain + re-poll required before next document",
        )
    return ("proceed", "all dispatches terminal, no active jobs, provider reachable")


# --- No retries after timeout (measurement policy) ---

CALIBRATION_RETRY_POLICY = {"timeout_retries": 0, "fallbacks": 0, "corrections": 0}


def should_retry_after_timeout(policy: dict[str, int] | None = None) -> bool:
    """Calibration never retries after a timeout. Default policy: no retry."""
    active = policy or CALIBRATION_RETRY_POLICY
    return int(active.get("timeout_retries", 0)) > 0


def should_fallback_or_correct(policy: dict[str, int] | None = None) -> bool:
    active = policy or CALIBRATION_RETRY_POLICY
    return int(active.get("fallbacks", 0)) > 0 or int(active.get("corrections", 0)) > 0


# --- Censored-sample handling and quantiles ---

def quantile_linear(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation quantile (type 7, numpy default). Requires sorted input."""
    if not sorted_vals:
        raise ValueError("empty sample")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0,1]")
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[int(pos)]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def summarize_durations(
    completed_s: list[float], censored_count: int = 0, label: str = "stage"
) -> dict[str, Any]:
    """Summarize completed durations alongside censored (not-completed) counts.

    Censored requests (stopped at the bound) are NEVER merged into completed
    timings. Quantiles use linear interpolation; tail estimates with n<20
    are flagged provisional. P95 is withheld (None) when censoring makes it
    indefensible: any censored sample at/above the bound invalidates the
    upper tail.
    """
    out: dict[str, Any] = {
        "label": label,
        "n_completed": len(completed_s),
        "n_censored": int(censored_count),
        "method": "linear-interpolation-type7",
        "provisional_tail": len(completed_s) < 20,
    }
    if not completed_s:
        out.update({"min": None, "median": None, "mean": None,
                    "p90": None, "p95": None, "max": None,
                    "p95_withheld": True,
                    "reason": "no completed samples"})
        return out
    vals = sorted(completed_s)
    out.update({
        "min": vals[0],
        "median": quantile_linear(vals, 0.5),
        "mean": statistics.fmean(vals),
        "p90": quantile_linear(vals, 0.9) if len(vals) >= 2 else vals[0],
        "max": vals[-1],
    })
    if censored_count > 0:
        out["p95"] = None
        out["p95_withheld"] = True
        out["reason"] = (f"{censored_count} censored sample(s) stopped at the bound; "
                         "upper tail indefensible — p95 withheld")
    elif len(vals) < 2:
        out["p95"] = vals[0]
        out["p95_withheld"] = False
    else:
        out["p95"] = quantile_linear(vals, 0.95)
        out["p95_withheld"] = False
    return out


def candidate_request_timeout(p95_s: float | None, factor: float = 1.25) -> float | None:
    """Starting heuristic P95*1.25. None when P95 was withheld."""
    if p95_s is None:
        return None
    return float(p95_s) * factor


# --- Storage isolation ---

PROTECTED_ROOTS = ("data/", "api/data/", "data-local/")


def assert_isolated_storage(path: str | Path, protected: tuple[str, ...] = PROTECTED_ROOTS) -> Path:
    """Raise when a benchmark path would touch protected History/sources/caches."""
    p = Path(str(path))
    text = str(p)
    for root in protected:
        if text == root.rstrip("/") or text.startswith(root) or f"/{root}" in text + "/":
            raise ValueError(f"benchmark storage {text!r} overlaps protected {root!r}")
    return p


def assert_catalog_isolated(kb_path: str | Path, live_catalog: str | Path) -> None:
    if Path(kb_path).resolve() == Path(live_catalog).resolve():
        raise ValueError("benchmark KB must be an isolated copy, never the live catalog")


# --- Local/cloud configuration separation ---

LOCAL_ONLY_KEYS = {
    "LLM_REQUEST_TIMEOUT_SECONDS",
    "ROUTER_TIMEOUT_SECONDS",
    "EXTRACTOR_TIMEOUT_SECONDS",
    "JUDGE_TIMEOUT_SECONDS",
    "OCR_TIMEOUT_SECONDS",
}
CLOUD_PRESERVED_KEYS = {
    "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL",
    "LLM_MAX_CONCURRENT_REQUESTS", "EXTRACTION_MAX_TOKENS",
}


def apply_local_profile(
    current: dict[str, Any], proposed: dict[str, Any], *, provider: str = "ollama-local"
) -> dict[str, Any]:
    """Return updated config touching only verified local timeout keys.

    Cloud identity, concurrency, model, output capacity, and validation keys
    are never modified; unknown keys raise instead of being silently added.
    """
    if provider != "ollama-local":
        raise ValueError(f"local profile applies only to ollama-local, got {provider!r}")
    unknown = set(proposed) - LOCAL_ONLY_KEYS
    if unknown:
        raise ValueError(f"refusing unverified keys: {sorted(unknown)}")
    protected = set(proposed) & CLOUD_PRESERVED_KEYS
    if protected:
        raise ValueError(f"refusing cloud-preserved keys: {sorted(protected)}")
    updated = dict(current)
    updated.update(proposed)
    return updated


# --- Failure/cache eligibility ---

def cache_eligible(outcome: str) -> bool:
    """Only fully completed extractions may enter the completed-result cache."""
    return outcome == "completed"


# --- Benchmark output and legacy loading ---

REQUIRED_MEASUREMENT_KEYS = {
    "source_id", "outcome", "dispatches", "ocr_seconds",
    "router_seconds", "extractor_seconds", "judge_seconds", "total_seconds",
}


def validate_measurement(record: dict[str, Any]) -> None:
    missing = REQUIRED_MEASUREMENT_KEYS - set(record)
    if missing:
        raise ValueError(f"measurement missing keys: {sorted(missing)}")
    if record["outcome"] not in TERMINAL_OUTCOMES + ("in_progress",):
        raise ValueError(f"unknown outcome {record['outcome']!r}")


def write_measurements(path: str | Path, records: list[dict[str, Any]]) -> Path:
    for record in records:
        validate_measurement(record)
    out = Path(str(path))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return out


def load_legacy_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Legacy payloads without diagnostics/dispatches still load with defaults."""
    loaded = dict(payload)
    loaded.setdefault("diagnostics", {})
    loaded.setdefault("dispatches", 0)
    return loaded


# --- Durable incremental progress (lost-progress fix) ---
#
# The 2026-09-16 sweep wrote measurements only at end-of-run, so the
# 60-minute task bound produced zero persisted records and an unknown
# dispatch count. This section makes progress durable incrementally:
#
# - doc-start records persist BEFORE processing (intent, not outcome);
# - terminal per-document records persist AS SOON AS each doc finishes;
# - every write is atomic (temp file + fsync + os.replace) and bounded
#   (tens of small JSON records — never document text, never credentials);
# - a killed run therefore always leaves an honest partial trail, and a
#   late empty report can never overwrite previously collected evidence.
#
# Recovery is load-only: load_progress() reads valid partial records
# without automatically resuming anything.

MEASUREMENTS_FILENAME = "measurements.json"
PROGRESS_LOG_FILENAME = "progress.jsonl"
PARTIAL_REPORT_FILENAME = "partial_report.json"
REPORT_FILENAME = "calibration_report.json"

CLIENT_TIMEOUT_DISCLAIMER = (
    "client timeout never proves server-side cancellation: the provider "
    "may still be generating after the client bound; no further inference "
    "is dispatched after the bound"
)

TERMINAL_OUTCOMES = ("completed", "partial", "failed", "cancelled",
                     "not_attempted", "interrupted")


def atomic_write_json(path: str | Path, obj: Any) -> Path:
    """Atomically replace path with obj serialized as JSON.

    Write-temp-in-same-directory + flush + os.fsync + os.replace: readers
    never observe a torn file, even if the process dies mid-write.
    """
    import os

    out = Path(str(path))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out)
    return out


def describe_dispatch_state(
    events: list[Any], intents: list[Any] | None = None
) -> dict[str, Any]:
    """Reconcile terminal dispatch events with intent records.

    - dispatches_known: terminal transport outcomes actually recorded;
    - dispatches_uncertain: intents with no terminal outcome (deadline /
      cancel / crash hit between intent and response) — explicitly
      uncertain, never silently zero;
    - per-outcome counts cover terminal events only.
    """
    known = len(events)
    by_outcome: dict[str, int] = {}
    for e in events:
        outcome = e.get("outcome") if isinstance(e, dict) else getattr(e, "outcome", "?")
        by_outcome[str(outcome)] = by_outcome.get(str(outcome), 0) + 1
    uncertain = 0
    intents_total = 0
    if intents is not None:
        intents_total = len(intents)
        for i in intents:
            outcome = i.get("outcome") if isinstance(i, dict) else getattr(i, "outcome", "uncertain")
            if outcome == "uncertain":
                uncertain += 1
    state: dict[str, Any] = {
        "dispatches_known": known,
        "dispatches_uncertain": uncertain,
        "dispatch_intents": intents_total,
        "terminal_by_outcome": by_outcome,
    }
    if known == 0 and uncertain == 0:
        state["note"] = ("no terminal dispatch recorded; an in-flight attempt "
                         "at a deadline leaves no terminal event — treat any "
                         "dispatch count here as unknown, not zero")
    return state


def build_partial_report(
    *,
    records: list[dict[str, Any]],
    order: list[str],
    in_progress: dict[str, Any] | None,
    dispatches_used_known: int,
    dispatches_uncertain: int,
    elapsed_s: float,
    deadline_fired: str,
    stop_reason: str,
    had_timeout_or_cancel: bool,
) -> dict[str, Any]:
    """Honest task-expiration report from persisted records only.

    Defensive per-record: one malformed record never breaks the report.
    Every planned document gets exactly one honest status: its terminal
    outcome, "interrupted" (started, never finished), or "not_attempted".
    Never fabricates timings — unknown stays unknown.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for r in records:
        try:
            sid = str(r.get("source_id", "?"))
            by_id[sid] = {
                "source_id": sid,
                "outcome": str(r.get("outcome", "unknown")),
                "dispatches": r.get("dispatches", "unknown"),
                "total_seconds": r.get("total_seconds", "unknown"),
                "phase": r.get("phase", "unknown"),
            }
        except Exception:  # noqa: BLE001 — one bad record must not kill the report
            continue
    in_progress_id: str | None = None
    if in_progress is not None:
        try:
            in_progress_id = str(in_progress.get("source_id", "?"))
        except Exception:  # noqa: BLE001
            in_progress_id = "?"
    doc_status: list[dict[str, str]] = []
    for sid in order:
        if sid in by_id:
            doc_status.append({"source_id": sid,
                               "status": by_id[sid]["outcome"]})
        elif sid == in_progress_id:
            doc_status.append({"source_id": sid, "status": "interrupted"})
        else:
            doc_status.append({"source_id": sid, "status": "not_attempted"})
    completed = [s for s in doc_status if s["status"] in ("completed", "partial")]
    report: dict[str, Any] = {
        "outcome": "partial",
        "stop_reason": stop_reason,
        "deadline_fired": deadline_fired,
        "elapsed_s": elapsed_s,
        "n_completed": len(completed),
        "n_planned": len(order),
        "documents": doc_status,
        "in_progress": in_progress_id,
        "dispatches_known": dispatches_used_known,
        "dispatches_uncertain": dispatches_uncertain,
        "server_cancellation": ("unknown — " + CLIENT_TIMEOUT_DISCLAIMER
                                if had_timeout_or_cancel else "no timeout/cancel observed"),
    }
    return report


def decide_apply(
    *,
    requested: bool,
    confirmed_in_same_run: bool,
    sufficient: bool,
    retry_fit_ok: bool,
    n_completed: int,
) -> dict[str, Any]:
    """Gate --apply on same-run confirmation evidence. Never raises.

    Insufficient evidence (too few completions, no same-run confirmation
    success, or retry-fit failure) refuses with an explicit reason instead
    of applying a proposal the measurements cannot defend.
    """
    if not requested:
        return {"applied": False, "reason": "not requested"}
    if n_completed == 0:
        return {"applied": False,
                "reason": "proposal reported, not applied — zero completed "
                          "measurements; nothing to apply"}
    if not sufficient:
        return {"applied": False,
                "reason": "proposal reported, not applied — sweep insufficient "
                          "for a defensible proposal"}
    if not confirmed_in_same_run:
        return {"applied": False,
                "reason": "proposal reported, not applied — confirmation did "
                          "not fully succeed in the same run"}
    if not retry_fit_ok:
        return {"applied": False,
                "reason": "proposal reported, not applied — candidate leaves "
                          "no room for the existing timeout retry; no-retry "
                          "measurements do not validate production retry timing"}
    return {"apply_approved": True}


class ProgressStore:
    """Durable incremental progress for one calibration run.

    Writes only inside the (isolated, caller-chosen) output directory —
    never History/sources/caches (assert_isolated_storage enforced).
    All file writes are best-effort and never raise: in-memory records
    always survive a write failure, and failures are exposed via
    write_errors for the partial report. A late empty state can never
    overwrite previously collected evidence (clobber guards).
    """

    def __init__(self, out_dir: str | Path) -> None:
        self.out_dir = Path(assert_isolated_storage(out_dir))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []
        self.in_progress: dict[str, Any] | None = None
        self.write_errors: list[str] = []
        self.notes: list[str] = []

    # -- mutation (in-memory first, then best-effort flush) --

    def record_start(self, source_id: str, intent: dict[str, Any] | None = None) -> None:
        """Persist a document-start record BEFORE processing (intent, not outcome)."""
        start = {"source_id": source_id, "event": "doc_start",
                 "intent": dict(intent or {})}
        self.in_progress = start
        self._append_log(start)
        self.flush_measurements()

    @staticmethod
    def _key(record: dict[str, Any]) -> tuple[str, str]:
        """Dedupe key: confirmation re-runs sweep documents, so the phase
        is part of the identity — a confirm outcome must never overwrite
        the sweep outcome for the same document."""
        try:
            return (str(record.get("source_id", "?")),
                    str(record.get("phase", "")))
        except Exception:  # noqa: BLE001
            return ("?", "")

    def record_outcome(self, record: dict[str, Any]) -> None:
        """Persist a terminal per-document record AS SOON AS it finishes."""
        key = self._key(record)
        self.records = [r for r in self.records if self._key(r) != key]
        self.records.append(dict(record))
        if self.in_progress is not None:
            try:
                started = self.in_progress
                if (str(started.get("source_id", "")) == key[0]
                        and str((started.get("intent") or {}).get("phase", "")) == key[1]):
                    self.in_progress = None
            except Exception:  # noqa: BLE001
                self.in_progress = None
        self._append_log({"event": "doc_outcome", "record": dict(record)})
        self.flush_measurements()

    def note(self, text: str) -> None:
        self.notes.append(str(text))
        self._append_log({"event": "note", "text": str(text)})

    # -- durable writes (never raise) --

    def _append_log(self, obj: dict[str, Any]) -> None:
        try:
            with open(self.out_dir / PROGRESS_LOG_FILENAME, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj) + "\n")
        except Exception as exc:  # noqa: BLE001 — best-effort; memory survives
            self.write_errors.append(f"progress-log append failed: {exc!r}")

    def flush_measurements(self) -> bool:
        """Atomically rewrite measurements.json from terminal records.

        Clobber guard: an empty in-memory state never overwrites a
        non-empty persisted file (a late empty report must not destroy
        previously collected evidence).
        """
        target = self.out_dir / MEASUREMENTS_FILENAME
        try:
            if not self.records:
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 — no usable existing file
                    existing = []
                if isinstance(existing, list) and len(existing) > 0:
                    self.write_errors.append(
                        "refused to overwrite non-empty measurements.json with empty state")
                    return False
            atomic_write_json(target, self.records)
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort; memory survives
            self.write_errors.append(f"measurements flush failed: {exc!r}")
            return False

    def write_partial_report(self, report: dict[str, Any]) -> bool:
        """Atomically write the expiration report, even if cleanup failed.

        Clobber guard: a report covering fewer terminal documents never
        replaces one covering more.
        """
        target = self.out_dir / PARTIAL_REPORT_FILENAME
        try:
            mine = sum(1 for d in (report.get("documents") or [])
                       if isinstance(d, dict) and d.get("status") not in
                       ("not_attempted",))
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                theirs = sum(1 for d in (existing.get("documents") or [])
                             if isinstance(d, dict) and d.get("status") not in
                             ("not_attempted",))
            except Exception:  # noqa: BLE001 — no usable existing report
                theirs = -1
            if mine < theirs:
                self.write_errors.append(
                    "refused to overwrite a more complete partial report with a thinner one")
                return False
            atomic_write_json(target, report)
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort; memory survives
            self.write_errors.append(f"partial-report write failed: {exc!r}")
            return False

    # -- recovery (load-only; never auto-resumes) --

    def snapshot(self) -> dict[str, Any]:
        return {"records": [dict(r) for r in self.records],
                "in_progress": dict(self.in_progress) if self.in_progress else None,
                "write_errors": list(self.write_errors),
                "notes": list(self.notes)}


def load_progress(out_dir: str | Path) -> dict[str, Any]:
    """Load valid partial records from a previous run. Load-only.

    Returns {"records", "events", "partial_report"} with only well-formed
    entries; malformed lines are skipped, never fatal. Loading never
    resumes or re-dispatches anything — the caller decides explicitly.
    """
    out = Path(str(out_dir))
    records: list[dict[str, Any]] = []
    try:
        data = json.loads((out / MEASUREMENTS_FILENAME).read_text(encoding="utf-8"))
        if isinstance(data, list):
            for r in data:
                try:
                    validate_measurement({k: r[k] for k in REQUIRED_MEASUREMENT_KEYS})
                    records.append(r)
                except Exception:  # noqa: BLE001 — skip invalid, keep valid
                    continue
    except Exception:  # noqa: BLE001 — missing/unreadable file = no records
        pass
    events: list[dict[str, Any]] = []
    try:
        with open(out / PROGRESS_LOG_FILENAME, encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except Exception:  # noqa: BLE001 — skip torn lines
                    continue
    except Exception:  # noqa: BLE001
        pass
    partial: dict[str, Any] | None = None
    try:
        data = json.loads((out / PARTIAL_REPORT_FILENAME).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            partial = data
    except Exception:  # noqa: BLE001
        pass
    return {"records": records, "events": events, "partial_report": partial}
