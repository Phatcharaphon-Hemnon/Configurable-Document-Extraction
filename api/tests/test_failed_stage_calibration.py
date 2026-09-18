"""Failed-stage calibration-ledger regressions (offline only).

The 2026-09-16 durable-measurement smoke run survived a partial document
(~334s, Extractor transport timeout) yet reported dispatches=0, timeouts=0,
attempt_outcomes={} and extractor_seconds=0.0. Root causes under test:

- R1: the harness read stage seconds from ``diagnostics.timings`` while the
  schema keeps durations on the document's top-level ``timings``.
- R2: service stages collect in INNER ``collect_dispatches`` scopes that
  shadowed the harness OUTER ledger, so returned-failure evidence never
  reached the report (nested scopes must propagate to the parent ledger;
  task isolation and no-double-counting preserved).
- R3: timed-out attempts left no duration trace in the record (censored
  observations must be reported with their measured durations while staying
  excluded from completed-sample statistics).

Mocked providers, temp storage only — never runtime data/ DBs, never live
inference, never gold answers as prompt/OCR input.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.documents import JudgeResult, RoutingDecision  # noqa: E402
from app.schemas.ocr import OCRPage  # noqa: E402
from app.services.client import Client, ClientError  # noqa: E402
from app.services.extraction_service import DocumentExtractionService  # noqa: E402
from app.services.request_control import (  # noqa: E402
    DispatchEvent,
    begin_dispatch_intent,
    collect_dispatch_intents,
    collect_dispatches,
    record_dispatch,
)
from app.services.timeout_calibration import (  # noqa: E402
    ProgressStore,
    decide_apply,
    load_progress,
    validate_measurement,
    write_measurements,
)


def _load_script():
    path = _API_DIR / "scripts" / "calibrate_timeouts.py"
    spec = importlib.util.spec_from_file_location("calib_failed_stage_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calib_failed_stage_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


SCRIPT = _load_script()

PAGE_TEXT = "\n".join([
    "ACME REPLAY CO",
    "TAX INVOICE No: INV-R1",
    "Total: 25.50",
])


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    for name, req in (("po_fields", "po_number"), ("delivery_note_fields", "delivery_number")):
        (kb / "field_catalog" / f"{name}.json").write_text(json.dumps({
            "doc_type": "x", "fields": [{"name": req, "type": "string", "required": True}],
        }), encoding="utf-8")
    return kb


def _service_settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings()
    s.knowledge_base_path = str(_kb(tmp_path))
    s.database_enabled = True
    s.database_path = str(tmp_path / "history.db")
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.result_cache_enabled = True
    s.result_cache_ttl_seconds = 3600.0
    s.result_cache_max_entries = 64
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _timeout_client() -> Client:
    """Real Client whose transport always stalls: every attempt records a
    terminal timeout dispatch event, then the generation raises."""
    from openai import APITimeoutError

    s = MagicMock(spec=Settings)
    s.llm_api_key = "test-token"
    s.llm_base_url = "https://failed-stage-calib.test.local/v1"
    s.llm_request_timeout_seconds = 30.0
    s.disable_strict_json_schema = False
    s.llm_max_concurrent_requests = 1
    client = Client(s)

    async def _stall(**kwargs):
        raise APITimeoutError(request=MagicMock())

    client._client.chat.completions.create = _stall
    return client


def _service_with_timeout_extractor(tmp_path: Path) -> DocumentExtractionService:
    service = DocumentExtractionService(settings=_service_settings(tmp_path))
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9, reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    for ext in service.extractors.values():
        ext._client = _timeout_client()
    # Offline OCR double (collecting fallback path): no transport, no model.
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=[PAGE_TEXT])
    service.ocr.last_pages = [OCRPage(text=PAGE_TEXT, blocks=[], preview=b"png")]
    service.ocr.model_hashes = {}
    service.ocr._hybrid_fingerprint = MagicMock(return_value={})
    return service


def _gold_env(tmp_path: Path, name: str = "smoke-partial.jpg"):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    (gold_dir / name).write_bytes(b"fake-image-bytes")
    by_name = {name: {"pages": [{"fields": {}}]}}
    return gold_dir, by_name, name


def _run_args(gold_dir, by_name, name, service, deadline_s=120.0):
    now = time.monotonic()
    return {"service": service, "settings": None, "gold_dir": gold_dir,
            "by_name": by_name, "name": name, "task_start": now,
            "task_deadline": now + deadline_s, "cold": True, "phase": "sweep"}


# ------------------------------------------------------------------
# Collector nesting: inner scopes must propagate to the parent ledger
# (finally-safe, on success AND on exception), without cross-task leaks.
# ------------------------------------------------------------------

def test_nested_dispatch_collectors_propagate_to_parent():
    with collect_dispatches() as outer:
        with collect_dispatches() as inner:
            record_dispatch(DispatchEvent(stage="extractor", model="m", tier=None,
                                          purpose="initial", duration_s=0.5,
                                          outcome="timeout", call_id="c1"))
            assert len(inner) == 1
            assert len(outer) == 0  # inner shadows while active (isolation)
    assert len(outer) == 1  # propagated on exit
    assert outer[0].outcome == "timeout"
    assert outer[0].duration_s == pytest.approx(0.5)


def test_nested_dispatch_collectors_propagate_on_exception():
    with collect_dispatches() as outer:
        with pytest.raises(RuntimeError):
            with collect_dispatches():
                record_dispatch(DispatchEvent(stage="extractor", model="m", tier=None,
                                              purpose="initial", duration_s=0.5,
                                              outcome="timeout", call_id="c1"))
                raise RuntimeError("boom after evidence")
    assert len(outer) == 1  # exit propagated despite the exception


def test_nested_intent_collectors_propagate_to_parent():
    with collect_dispatch_intents() as outer:
        with collect_dispatch_intents() as inner:
            intent = begin_dispatch_intent(stage="extractor", model="m", tier=None,
                                           purpose="initial", call_id="c1")
            assert intent is not None
            assert len(inner) == 1
        # inner exit propagates the (still uncertain) intent outward
    assert len(outer) == 1
    assert outer[0].call_id == "c1"


def test_parentless_inner_collector_discards_safely():
    with collect_dispatches() as only:
        record_dispatch(DispatchEvent(stage="s", model="m", tier=None,
                                      purpose="initial", duration_s=0.1,
                                      outcome="ok", call_id="c"))
    assert len(only) == 1  # kept locally; no parent to notify, no crash


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_separate_ledgers_with_nesting():
    async def run(tag: str):
        with collect_dispatches() as outer:
            with collect_dispatches():
                await asyncio.sleep(0.01)
                record_dispatch(DispatchEvent(stage="extractor", model=tag, tier=None,
                                              purpose="initial", duration_s=0.1,
                                              outcome="ok", call_id=tag))
            return [e.model for e in outer]

    models = await asyncio.gather(run("a"), run("b"), run("c"))
    assert models == [["a"], ["b"], ["c"]]  # exact, no leakage, no doubling


# ------------------------------------------------------------------
# Real pipeline: provider timeout is caught into failed_stage=extractor
# (never raised), and the evidence survives nested collector scopes.
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_timeout_returns_failed_stage_and_survives_nested_scopes(
    tmp_path,
):
    service = _service_with_timeout_extractor(tmp_path)
    with collect_dispatches() as outer_events:
        with collect_dispatch_intents() as outer_intents:
            doc = await service._extract_one_page(
                filename="t.png", page_text=PAGE_TEXT, page_number=1)
    # The pipeline catches the provider timeout into a returned failure.
    assert doc.failed_stage == "extractor"
    assert doc.needs_review is True
    assert doc.judge_status == "unavailable"
    assert "timed out" in (doc.error or "").lower()
    # Stage timing persists on the document even with no response.
    assert doc.timings.get("extractor") is not None
    assert doc.timings["extractor"] >= 0
    # Document-level counts come from the inner ledger (exact, authoritative).
    usage = doc.usage.get("extractor_invoice", {})
    assert usage.get("dispatches", 0) >= 1
    assert usage.get("timeouts", 0) >= 1
    # Harness-level outer scopes observe the same evidence (nested
    # propagation): the smoke gap was outer == [] despite the timeout.
    assert len(outer_events) >= 1
    assert any(e.outcome == "timeout" and e.stage == "extractor"
               for e in outer_events)
    assert len(outer_intents) >= 1


@pytest.mark.asyncio
async def test_harness_record_captures_returned_extractor_failure(tmp_path):
    """End-to-end offline: real service + scripted timeout transport through
    the calibration harness record builder (the smoke's failed_stage path)."""
    service = _service_with_timeout_extractor(tmp_path)
    gold_dir, by_name, name = _gold_env(tmp_path)
    rec, used, had_timeout = await SCRIPT._run_one_doc(
        **_run_args(gold_dir, by_name, name, service))
    assert rec["outcome"] == "partial"
    assert "Extractor failed" in " ".join(rec["validation_errors"])
    assert rec["judge_status"] == "unavailable"
    # Smoke gap: these were all zero/empty despite the timeout.
    assert rec["dispatches"] >= 1 and used == rec["dispatches"]
    assert rec["timeouts"] >= 1
    assert had_timeout is True
    assert "timeout" in (rec["attempt_outcomes"].get("extractor") or [])
    detail = rec["dispatch_detail"]
    assert detail["dispatches_known"] == rec["dispatches"]
    assert detail["dispatches_uncertain"] == 0
    # Stage duration measured on the document (top-level timings), not 0.0
    # from the wrong location; judge never ran so zero is valid there.
    assert rec["extractor_seconds"] is not None and rec["extractor_seconds"] >= 0
    assert rec["stage_timing_status"]["extractor"] == "measured"
    assert rec["router_seconds"] is not None
    assert rec["judge_seconds"] == 0.0
    assert rec["stage_timing_status"]["judge"] == "not_run"
    # Censored attempts carry measured durations yet stay out of the
    # completed-sample statistics inputs.
    assert len(rec["censored_durations"].get("extractor", [])) >= 1


@pytest.mark.asyncio
async def test_harness_record_captures_raised_failure_with_nested_scope(tmp_path):
    """A service that records into an inner scope then raises: the harness
    record must keep the terminal evidence (finally-safe propagation)."""

    class _RaiseAfterEvidenceService:
        async def extract_group(self, parts, force_refresh=True):
            with collect_dispatches():
                record_dispatch(DispatchEvent(
                    stage="extractor", model="m", tier="plain",
                    purpose="initial", duration_s=0.5,
                    outcome="timeout", call_id="c9"))
                raise ClientError("LLM request timed out")

    gold_dir, by_name, name = _gold_env(tmp_path)
    rec, used, _ = await SCRIPT._run_one_doc(
        **_run_args(gold_dir, by_name, name, _RaiseAfterEvidenceService()))
    assert rec["outcome"] == "failed"
    assert rec["dispatches"] == 1 and used == 1
    assert rec["timeouts"] == 1
    assert rec["attempt_outcomes"] == {"extractor": ["timeout"]}
    # The stage never produced a measured block duration on this path:
    # unknown (null), never a fabricated zero.
    assert rec["extractor_seconds"] is None
    assert rec["stage_timing_status"]["extractor"] == "unknown"


@pytest.mark.asyncio
async def test_harness_record_captures_cancellation_without_timeout_claim(tmp_path):
    """Operator cancel inside a nested scope: interrupted, evidence kept,
    never reported as a timeout."""

    class _CancelAfterEvidenceService:
        async def extract_group(self, parts, force_refresh=True):
            with collect_dispatches():
                record_dispatch(DispatchEvent(
                    stage="router", model="m", tier="plain",
                    purpose="initial", duration_s=0.1,
                    outcome="ok", call_id="c1"))
                raise asyncio.CancelledError()

    gold_dir, by_name, name = _gold_env(tmp_path)
    with pytest.raises(SCRIPT.DocInterrupted) as excinfo:
        await SCRIPT._run_one_doc(
            **_run_args(gold_dir, by_name, name, _CancelAfterEvidenceService()))
    rec = excinfo.value.record
    assert rec["outcome"] == "interrupted"
    assert rec["dispatches"] == 1
    assert rec["timeouts"] == 0
    assert rec["stage_timing_status"]["router"] == "unknown"
    assert rec["extractor_seconds"] is None


@pytest.mark.asyncio
async def test_harness_predispatch_failure_reports_zero_without_false_timing(tmp_path):
    """Failure before any dispatch: zero is valid (no operation occurred)
    and no stage claims a measurement."""

    class _PreDispatchFailService:
        async def extract_group(self, parts, force_refresh=True):
            raise RuntimeError("no readable text on this page")

    gold_dir, by_name, name = _gold_env(tmp_path)
    rec, used, had_timeout = await SCRIPT._run_one_doc(
        **_run_args(gold_dir, by_name, name, _PreDispatchFailService()))
    assert rec["outcome"] == "failed"
    assert rec["dispatches"] == 0 and used == 0
    assert had_timeout is False
    assert rec["timeouts"] == 0 and rec["errors"] == 0
    assert rec["stage_timing_status"]["extractor"] != "measured"
    assert rec["extractor_seconds"] in (0.0, None)


@pytest.mark.asyncio
async def test_sequential_docs_do_not_leak_or_double_count(tmp_path):
    """Two harness records in one process: each carries exactly its own
    evidence (no cross-job leakage, no propagation doubling)."""
    service = _service_with_timeout_extractor(tmp_path)
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    by_name = {}
    recs = []
    for name in ("leak-a.jpg", "leak-b.jpg"):
        (gold_dir / name).write_bytes(b"fake-image-bytes")
        by_name[name] = {"pages": [{"fields": {}}]}
        rec, used, _ = await SCRIPT._run_one_doc(
            **_run_args(gold_dir, by_name, name, service))
        recs.append((rec, used))
    assert recs[0][0]["dispatches"] == recs[1][0]["dispatches"]
    assert recs[0][0]["dispatches"] >= 1
    assert recs[0][1] == recs[0][0]["dispatches"]
    assert recs[0][0]["dispatch_detail"]["dispatches_known"] == recs[0][0]["dispatches"]


def test_new_record_shape_survives_store_roundtrip_and_validation(tmp_path):
    """Report serialization + restart recovery for the honest record shape
    (null stage seconds, censored durations, per-stage timing status)."""
    out = tmp_path / "calib"
    store = ProgressStore(out)
    rec = {"source_id": "a.jpg", "outcome": "partial", "phase": "sweep",
           "dispatches": 2, "ocr_seconds": 0.1, "router_seconds": 0.05,
           "extractor_seconds": None, "judge_seconds": 0.0,
           "total_seconds": 4.0, "effective_request_s": 60.0,
           "attempt_durations": {"router": [0.05], "extractor": []},
           "censored_durations": {"extractor": [3.9]},
           "stage_timing_status": {"router": "measured", "extractor": "unknown",
                                   "judge": "not_run"},
           "attempt_outcomes": {"router": ["ok"], "extractor": ["timeout"]},
           "timeouts": 1, "errors": 0}
    store.record_outcome(rec)
    out_file = write_measurements(out / "copy.json", [rec])
    assert out_file.exists()
    validate_measurement({k: rec[k] for k in
                          ("source_id", "outcome", "dispatches",
                           "ocr_seconds", "router_seconds",
                           "extractor_seconds", "judge_seconds",
                           "total_seconds")})
    recovered = load_progress(out)
    assert len(recovered["records"]) == 1
    back = recovered["records"][0]
    assert back["extractor_seconds"] is None
    assert back["censored_durations"] == {"extractor": [3.9]}
    assert back["stage_timing_status"]["extractor"] == "unknown"


def test_censored_timeouts_excluded_from_proposals_but_counted():
    ok_completed = {
        "source_id": "ok.jpg", "outcome": "completed", "phase": "sweep",
        "dispatches": 2, "judge_status": "passed",
        "attempt_durations": {"router": [0.5], "extractor": [1.0], "judge": [0.4]},
        "attempt_outcomes": {"router": ["ok"], "extractor": ["ok"], "judge": ["ok"]},
    }
    partial_timeout = {
        "source_id": "slow.jpg", "outcome": "partial", "phase": "sweep",
        "dispatches": 2, "judge_status": "unavailable",
        "attempt_durations": {"router": [0.4], "extractor": []},
        "censored_durations": {"extractor": [300.1]},
        "attempt_outcomes": {"router": ["ok"], "extractor": ["timeout", "timeout"]},
    }
    summary, candidates = SCRIPT._summarize([ok_completed, partial_timeout])
    ext = summary["extractor"]
    # Only the completed 1.0s sample feeds statistics; the 300.1s timeouts
    # are censored (counted, never merged).
    assert ext["n_completed"] == 1
    assert ext["n_censored"] == 2
    assert ext["p95"] is None and ext["p95_withheld"] is True
    assert candidates["extractor"] is None
    proposals = SCRIPT._derive_proposals(summary, {
        "request_timeout_s": 45.0, "router_timeout_s": 100.0,
        "extractor_timeout_s": 150.0, "judge_timeout_s": 100.0})
    assert proposals["extractor"]["withheld"] is True
    assert proposals["extractor"]["candidate_request_s"] is None
    gate = decide_apply(requested=True, confirmed_in_same_run=False,
                        sufficient=False, retry_fit_ok=False, n_completed=1)
    assert gate["applied"] is False
