"""Fault-injection regressions for the timeout-calibration lost-progress fix.

Offline only: mocked transports/services, temp storage, SHORT test
deadlines (never hour-long waits). Never runtime data/DBs, never live
inference, never gold answers as prompt/OCR input.

Covers the 2026-09-16 failure (60-minute sweep, zero persisted records,
unknown dispatch count):
- per-document outcomes persist incrementally, even on timeout/cancel;
- dispatch evidence is never discarded (known vs explicitly uncertain);
- operator cancellation is never reported as a timeout;
- report-generation failure still leaves a partial report;
- process exit after a checkpoint recovers without auto-resuming;
- dispatch/time caps stay shared across sweep and confirmation;
- insufficient evidence refuses --apply.
Plus offline deadline-enforcement checks (stalls, blocking work,
queue attribution, cancellation delivery, cleanup honesty).
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

import app.services.timeout_calibration as tc  # noqa: E402
from app.schemas.llm_control import RequestBudget  # noqa: E402
from app.services.request_control import (  # noqa: E402
    DISPATCH_OUTCOME_OK,
    DISPATCH_OUTCOME_TIMEOUT,
    DISPATCH_OUTCOME_UNCERTAIN,
    DispatchEvent,
    attempt_ceiling_s,
    begin_dispatch_intent,
    collect_dispatch_intents,
    collect_dispatches,
    record_dispatch,
    request_budget,
    settle_dispatch_intent,
)
from app.services.timeout_calibration import (  # noqa: E402
    ProgressStore,
    build_partial_report,
    decide_apply,
    describe_dispatch_state,
    load_progress,
    plan_confirmation,
)


def _load_script():
    path = _API_DIR / "scripts" / "calibrate_timeouts.py"
    spec = importlib.util.spec_from_file_location("calib_harness_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calib_harness_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


SCRIPT = _load_script()


# --- offline doubles ----------------------------------------------------

def _gold_env(tmp_path: Path, name: str = "doc1.jpg"):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    (gold_dir / name).write_bytes(b"fake-image-bytes")
    by_name = {name: {"pages": [{"fields": {}}]}}
    return gold_dir, by_name, name


class _Dump:
    def __init__(self, documents):
        self._documents = documents

    def model_dump(self, mode="json"):
        return {"documents": self._documents}


def _completed_doc():
    return {"fields": [], "validation_errors": [],
            "judge_status": "skipped", "diagnostics": {"timings": {}}}


class _FastService:
    """Completes immediately after recording two terminal dispatches."""

    async def extract_group(self, parts, force_refresh=True):
        record_dispatch(DispatchEvent(stage="router", model="m", tier="plain",
                                      purpose="initial", duration_s=0.01,
                                      outcome="ok", call_id="c1"))
        record_dispatch(DispatchEvent(stage="extractor", model="m", tier="plain",
                                      purpose="initial", duration_s=0.02,
                                      outcome="ok", call_id="c1"))
        return _Dump([_completed_doc()])


class _HangWithEvidenceService:
    """Records terminal dispatches + one unsettled intent, then hangs."""

    async def extract_group(self, parts, force_refresh=True):
        record_dispatch(DispatchEvent(stage="router", model="m", tier="plain",
                                      purpose="initial", duration_s=0.01,
                                      outcome="ok", call_id="c1"))
        record_dispatch(DispatchEvent(stage="extractor", model="m", tier="plain",
                                      purpose="initial", duration_s=0.02,
                                      outcome="ok", call_id="c1"))
        begin_dispatch_intent(stage="extractor", model="m", tier="plain",
                              purpose="initial", call_id="c1")
        await asyncio.sleep(60.0)
        return _Dump([_completed_doc()])


class _HangService:
    async def extract_group(self, parts, force_refresh=True):
        await asyncio.sleep(60.0)
        return _Dump([_completed_doc()])


def _run_args(gold_dir, by_name, name, deadline_s=60.0):
    now = time.monotonic()
    return {"service": None, "settings": None, "gold_dir": gold_dir,
            "by_name": by_name, "name": name, "task_start": now,
            "task_deadline": now + deadline_s, "cold": True, "phase": "sweep"}


# --- intent ledger (default-off, first-settle-wins) ----------------------

def test_intent_ledger_default_off_records_nothing():
    assert attempt_ceiling_s.get() is None
    assert begin_dispatch_intent(stage="s", model="m", tier=None,
                                 purpose="initial", call_id="") is None
    settle_dispatch_intent(None, "ok")  # no-op, never raises


def test_intent_settle_first_wins_never_overwrites_terminal():
    with collect_dispatch_intents() as intents:
        intent = begin_dispatch_intent(stage="extractor", model="m", tier="plain",
                                       purpose="initial", call_id="c1")
        assert intent is not None and intent.outcome == DISPATCH_OUTCOME_UNCERTAIN
        settle_dispatch_intent(intent, DISPATCH_OUTCOME_OK, time.perf_counter())
        assert intent.outcome == DISPATCH_OUTCOME_OK
        settle_dispatch_intent(intent, DISPATCH_OUTCOME_UNCERTAIN)
        assert intent.outcome == DISPATCH_OUTCOME_OK  # terminal kept
    assert len(intents) == 1


def test_describe_dispatch_state_known_vs_uncertain():
    events = [{"outcome": "ok"}, {"outcome": "ok"}]
    intents = [{"outcome": "ok"}, {"outcome": "uncertain"}]
    state = describe_dispatch_state(events, intents)
    assert state["dispatches_known"] == 2
    assert state["dispatches_uncertain"] == 1
    assert state["dispatch_intents"] == 2
    empty = describe_dispatch_state([], [])
    assert empty["dispatches_known"] == 0
    assert "unknown, not zero" in empty["note"]


# --- fault injection: timeout keeps evidence -----------------------------

@pytest.mark.asyncio
async def test_doc_deadline_keeps_known_dispatches_and_flags_uncertain(
    tmp_path, monkeypatch
):
    """First attempt times out before the document completes: terminal
    events captured so far are kept (never 0), the in-flight intent is
    explicitly uncertain, outcome is cancelled (deadline), not failed."""
    monkeypatch.setattr(SCRIPT, "DOC_LIMIT_S", 0.05)  # wait_for floor is 1.0s
    gold_dir, by_name, name = _gold_env(tmp_path)
    args = _run_args(gold_dir, by_name, name)
    args["service"] = _HangWithEvidenceService()
    rec, used, had_timeout = await SCRIPT._run_one_doc(**args)
    assert rec["outcome"] == "cancelled"
    assert had_timeout is True
    assert used == 2 and rec["dispatches"] == 2  # known kept, not discarded
    detail = rec["dispatch_detail"]
    assert detail["dispatches_known"] == 2
    assert detail["dispatches_uncertain"] == 1  # unsettled intent
    assert "server-side cancellation" in detail["inflight"]


@pytest.mark.asyncio
async def test_operator_cancel_is_interrupted_never_timeout(tmp_path, monkeypatch):
    """Cancellation during a document: DocInterrupted carries the partial
    record (outcome interrupted); CancelledError is never a timeout."""
    monkeypatch.setattr(SCRIPT, "DOC_LIMIT_S", 60.0)
    gold_dir, by_name, name = _gold_env(tmp_path)
    args = _run_args(gold_dir, by_name, name, deadline_s=60.0)
    args["service"] = _HangService()
    task = asyncio.ensure_future(SCRIPT._run_one_doc(**args))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(SCRIPT.DocInterrupted) as excinfo:
        await task
    rec = excinfo.value.record
    assert rec["outcome"] == "interrupted"
    assert rec["outcome"] != "timeout"
    assert rec["dispatch_detail"]["dispatches_uncertain"] == 0  # no intent began


# --- fault injection: one completes, next hangs, deadline expires --------

@pytest.mark.asyncio
async def test_completed_survives_hang_and_every_doc_has_honest_status(
    tmp_path, monkeypatch
):
    """Sweep-level flow with a ProgressStore: doc1 completes, doc2 hits the
    doc deadline, doc3 never attempted. Recovery sees terminal records for
    1+2 and not_attempted/interrupted for the rest — never silent."""
    monkeypatch.setattr(SCRIPT, "DOC_LIMIT_S", 0.05)
    out = tmp_path / "calib"
    store = ProgressStore(out)
    order = ["doc1.jpg", "doc2.jpg", "doc3.jpg"]
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    by_name = {}
    for n in order:
        (gold_dir / n).write_bytes(b"x")
        by_name[n] = {"pages": [{"fields": {}}]}

    records = []
    store.record_start("doc1.jpg", {"phase": "sweep", "index": 0})
    rec1, used1, _ = await SCRIPT._run_one_doc(
        **{**_run_args(gold_dir, by_name, "doc1.jpg"), "service": _FastService()})
    assert rec1["outcome"] == "completed"
    records.append(rec1)
    store.record_outcome(rec1)

    store.record_start("doc2.jpg", {"phase": "sweep", "index": 1})
    rec2, used2, had_timeout = await SCRIPT._run_one_doc(
        **{**_run_args(gold_dir, by_name, "doc2.jpg"),
           "service": _HangWithEvidenceService()})
    assert rec2["outcome"] == "cancelled" and had_timeout
    records.append(rec2)
    store.record_outcome(rec2)
    assert used1 == 2 and used2 == 2  # known counts on both paths

    # Simulate process exit after the checkpoint: fresh eyes on disk.
    recovered = load_progress(out)
    assert len(recovered["records"]) == 2
    partial = build_partial_report(
        records=recovered["records"], order=order, in_progress=None,
        dispatches_used_known=used1 + used2, dispatches_uncertain=1,
        elapsed_s=3.0, deadline_fired="experimental document deadline 600s",
        stop_reason="two consecutive experimental deadlines (simulated)",
        had_timeout_or_cancel=True)
    by_id = {d["source_id"]: d["status"] for d in partial["documents"]}
    assert by_id == {"doc1.jpg": "completed", "doc2.jpg": "cancelled",
                     "doc3.jpg": "not_attempted"}
    assert partial["dispatches_known"] == 4
    assert partial["dispatches_uncertain"] == 1
    assert "server-side cancellation" in partial["server_cancellation"]
    # Recovery never auto-resumes: a fresh store starts empty.
    assert ProgressStore(tmp_path / "fresh").snapshot()["records"] == []


# --- fault injection: cancellation/failure during result writing ---------

def test_write_failure_never_loses_in_memory_records(tmp_path, monkeypatch):
    """Cancellation during result writing (flush raises): earlier records
    survive in memory, the failure is recorded, and a later flush sends."""
    out = tmp_path / "calib"
    store = ProgressStore(out)
    rec = {"source_id": "a.jpg", "outcome": "completed", "dispatches": 1,
           "ocr_seconds": 0.1, "router_seconds": 0.1, "extractor_seconds": 0.2,
           "judge_seconds": 0.0, "total_seconds": 0.4}
    store.record_outcome(rec)  # durable first write succeeds
    assert (out / "measurements.json").exists()

    def _boom(path, obj):
        raise RuntimeError("simulated crash during write")

    monkeypatch.setattr(tc, "atomic_write_json", _boom)
    rec2 = dict(rec, source_id="b.jpg")
    store.record_outcome(rec2)  # must not raise
    assert [r["source_id"] for r in store.records] == ["a.jpg", "b.jpg"]
    assert any("failed" in e for e in store.write_errors)

    monkeypatch.undo()
    assert store.flush_measurements() is True
    on_disk = json.loads((out / "measurements.json").read_text())
    assert [r["source_id"] for r in on_disk] == ["a.jpg", "b.jpg"]


def test_empty_state_never_overwrites_collected_evidence(tmp_path):
    """A late empty report must not destroy previously collected evidence:
    flushing an empty store over a non-empty file is refused; the fuller
    partial report always wins."""
    out = tmp_path / "calib"
    full = ProgressStore(out)
    rec = {"source_id": "a.jpg", "outcome": "completed", "dispatches": 1,
           "ocr_seconds": 0.1, "router_seconds": 0.1, "extractor_seconds": 0.2,
           "judge_seconds": 0.0, "total_seconds": 0.4}
    full.record_outcome(rec)
    assert full.write_partial_report(build_partial_report(
        records=[rec], order=["a.jpg", "b.jpg"], in_progress=None,
        dispatches_used_known=1, dispatches_uncertain=0, elapsed_s=1.0,
        deadline_fired="none", stop_reason="ok", had_timeout_or_cancel=False))

    empty = ProgressStore(out)
    assert empty.flush_measurements() is False  # refused
    assert "refused to overwrite" in " ".join(empty.write_errors)
    thinner = build_partial_report(
        records=[], order=["a.jpg", "b.jpg"], in_progress=None,
        dispatches_used_known=0, dispatches_uncertain=0, elapsed_s=0.1,
        deadline_fired="none", stop_reason="late empty", had_timeout_or_cancel=False)
    assert empty.write_partial_report(thinner) is False  # thinner loses
    assert [r["source_id"] for r in load_progress(out)["records"]] == ["a.jpg"]


def test_report_generation_from_garbage_records_still_reports(tmp_path):
    """Malformed records never break the expiration report: every planned
    document still gets an honest terminal or not-attempted status."""
    order = ["a.jpg", "b.jpg"]
    garbage = [{"source_id": "a.jpg"}, {"nonsense": object()}]
    partial = build_partial_report(
        records=garbage, order=order, in_progress={"source_id": "b.jpg"},
        dispatches_used_known=0, dispatches_uncertain=0, elapsed_s=9.0,
        deadline_fired="task time budget exhausted",
        stop_reason="simulated reporting-path failure",
        had_timeout_or_cancel=True)
    by_id = {d["source_id"]: d["status"] for d in partial["documents"]}
    assert by_id["a.jpg"] == "unknown"  # present but outcome unknown
    assert by_id["b.jpg"] == "interrupted"  # started, never finished
    out = tmp_path / "calib"
    assert ProgressStore(out).write_partial_report(partial) is True


def test_load_progress_skips_invalid_without_resuming(tmp_path):
    """Recovery loads valid partials, skips torn/invalid entries, and
    never auto-resumes anything (load is read-only)."""
    out = tmp_path / "calib"
    out.mkdir(parents=True, exist_ok=True)
    good = {"source_id": "a.jpg", "outcome": "completed", "dispatches": 1,
            "ocr_seconds": 0.1, "router_seconds": 0.1, "extractor_seconds": 0.2,
            "judge_seconds": 0.0, "total_seconds": 0.4}
    bad = {"source_id": "b.jpg", "outcome": "completed"}  # missing keys
    (out / "measurements.json").write_text(json.dumps([good, bad]))
    with open(out / "progress.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "doc_start", "source_id": "a.jpg"}) + "\n")
        fh.write("{torn line\n")
    loaded = load_progress(out)
    assert [r["source_id"] for r in loaded["records"]] == ["a.jpg"]
    assert len(loaded["events"]) == 1
    assert loaded["partial_report"] is None


# --- gates: shared caps and insufficient-evidence apply refusal ----------

def test_shared_caps_stop_confirmation_when_sweep_exhausted_them():
    records = [
        {"source_id": "a.jpg", "outcome": "completed", "total_seconds": 50.0},
        {"source_id": "b.jpg", "outcome": "completed", "total_seconds": 60.0},
    ]
    order = ["a.jpg", "b.jpg"]
    # Sweep consumed the whole dispatch budget: no confirmation dispatches left.
    assert plan_confirmation(records, order, dispatches_used=60,
                             elapsed_s=100.0) == []
    # Sweep consumed the whole time budget: no confirmation time left.
    assert plan_confirmation(records, order, dispatches_used=10,
                             elapsed_s=3600.0) == []


def test_insufficient_evidence_prevents_apply():
    assert decide_apply(requested=False, confirmed_in_same_run=False,
                        sufficient=False, retry_fit_ok=False,
                        n_completed=0)["applied"] is False
    zero = decide_apply(requested=True, confirmed_in_same_run=False,
                        sufficient=False, retry_fit_ok=False, n_completed=0)
    assert zero["applied"] is False and "zero completed" in zero["reason"]
    thin = decide_apply(requested=True, confirmed_in_same_run=True,
                        sufficient=False, retry_fit_ok=True, n_completed=2)
    assert thin["applied"] is False and "insufficient" in thin["reason"]
    unconfirmed = decide_apply(requested=True, confirmed_in_same_run=False,
                               sufficient=True, retry_fit_ok=True, n_completed=6)
    assert unconfirmed["applied"] is False and "confirmation" in unconfirmed["reason"]
    noroom = decide_apply(requested=True, confirmed_in_same_run=True,
                          sufficient=True, retry_fit_ok=False, n_completed=6)
    assert noroom["applied"] is False and "retry" in noroom["reason"]
    ok = decide_apply(requested=True, confirmed_in_same_run=True,
                      sufficient=True, retry_fit_ok=True, n_completed=6)
    assert ok.get("apply_approved") is True


# --- deadline enforcement with offline doubles ---------------------------

@pytest.mark.asyncio
async def test_transport_stall_settles_timeout_not_cancel(monkeypatch):
    """Connect/read stall at the transport boundary: a raising transport
    records a terminal timeout (settled intent), never uncertainty."""
    import app.services.client as client_mod

    cli = object.__new__(client_mod.Client)
    cli._timeout = 30.0

    async def _stall(**kwargs):
        raise asyncio.TimeoutError("simulated read stall")

    cli._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_stall)))
    monkeypatch.setattr(client_mod, "TIMEOUT_MAX_RETRIES", 0)
    token = request_budget.set(RequestBudget())
    try:
        with collect_dispatches() as events:
            with collect_dispatch_intents() as intents:
                with pytest.raises(asyncio.TimeoutError):
                    await cli._chat_with_retry(model="m")
    finally:
        request_budget.reset(token)
    assert [e.outcome for e in events] == [DISPATCH_OUTCOME_TIMEOUT]
    assert [i.outcome for i in intents] == [DISPATCH_OUTCOME_TIMEOUT]


@pytest.mark.asyncio
async def test_transport_ok_settles_intent_and_event():
    """Happy path with the ledger active: intent and terminal event agree
    (ok); the per-attempt finally must not overwrite the terminal outcome."""
    import app.services.client as client_mod

    cli = object.__new__(client_mod.Client)
    cli._timeout = 30.0

    async def _ok(**kwargs):
        return SimpleNamespace(id="chatcmpl-test")

    cli._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_ok)))
    token = request_budget.set(RequestBudget())
    try:
        with collect_dispatches() as events:
            with collect_dispatch_intents() as intents:
                resp = await cli._chat_with_retry(model="m")
                assert resp.id == "chatcmpl-test"
    finally:
        request_budget.reset(token)
    assert [e.outcome for e in events] == [DISPATCH_OUTCOME_OK]
    assert [i.outcome for i in intents] == [DISPATCH_OUTCOME_OK]


@pytest.mark.asyncio
async def test_transport_cancel_leaves_intent_explicitly_uncertain():
    """Outer deadline cancelling an in-flight attempt: no terminal event,
    intent stays explicitly uncertain (never silent, never ok)."""
    import app.services.client as client_mod

    cli = object.__new__(client_mod.Client)
    cli._timeout = 30.0

    async def _hang(**kwargs):
        await asyncio.sleep(60.0)
        return object()

    cli._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_hang)))
    token = request_budget.set(RequestBudget())
    try:
        with collect_dispatches() as events:
            with collect_dispatch_intents() as intents:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(cli._chat_with_retry(model="m"),
                                           timeout=0.2)
    finally:
        request_budget.reset(token)
    assert events == []  # cancelled before any terminal record
    assert [i.outcome for i in intents] == [DISPATCH_OUTCOME_UNCERTAIN]


@pytest.mark.asyncio
async def test_idle_gate_is_async_and_cancellation_is_prompt(monkeypatch):
    """The idle gate must not block the event loop: cancellation during
    the post-timeout drain is delivered promptly (the old blocking sleep
    held the loop for the full drain past any deadline)."""
    assert inspect.iscoroutinefunction(SCRIPT._idle_gate)

    async def _fast_ps(base_url, route, timeout=10):
        return {"reachable": True, "data": {"models": []}}

    monkeypatch.setattr(SCRIPT, "_ollama_async", _fast_ps)
    monkeypatch.setattr(SCRIPT, "_active_app_jobs", lambda settings: [])
    monkeypatch.setattr(SCRIPT, "CONFIRM_DRAIN_S", 30.0)
    settings = SimpleNamespace(llm_base_url="http://localhost:11434/v1")
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            SCRIPT._idle_gate(settings=settings, had_timeout_or_cancel=True,
                              task_deadline=time.monotonic() + 60.0),
            timeout=2.0)
    assert time.monotonic() - t0 < 5.0  # drain abandoned, not slept through


@pytest.mark.asyncio
async def test_blocking_probe_does_not_hang_the_gate(monkeypatch):
    """A blocking provider probe (slow connect) is isolated off-loop with
    its own bound, so the gate — and task deadlines — survive it."""
    def _blocked(base_url, route, timeout=10):
        time.sleep(30.0)
        return {"reachable": True, "data": {}}

    monkeypatch.setattr(SCRIPT, "_ollama", _blocked)
    t0 = time.monotonic()
    # timeout=0 keeps the inner bound tiny (0+5s outer); must still return.
    res = await SCRIPT._ollama_async("http://localhost:11434/v1", "/api/ps",
                                     timeout=0)
    assert res["reachable"] is False
    assert time.monotonic() - t0 < 10.0


def test_queue_wait_attribution_offline():
    """Queue-dominated stages attribute to queue, never to request/stage."""
    assert tc.attribute_deadline(queue_wait_s=9.0, attempt_s=2.0,
                                 request_timeout_s=45.0, stage_elapsed_s=11.0,
                                 stage_limit_s=150.0) == "queue"


def test_cancelled_outcome_passes_validation_and_is_not_timeout():
    rec = {"source_id": "a.jpg", "outcome": "interrupted", "dispatches": 1,
           "ocr_seconds": 0.1, "router_seconds": 0.1, "extractor_seconds": 0.2,
           "judge_seconds": 0.0, "total_seconds": 0.4}
    tc.validate_measurement(rec)  # honest terminal status, loadable
