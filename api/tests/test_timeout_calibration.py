"""RED-first regressions for the bounded local timeout-calibration benchmark.

Mocked providers, temp storage only — never runtime data/ DBs, never live
inference, never gold answers as prompt/OCR input.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.services.request_control import attempt_ceiling_s  # noqa: E402
from app.services.timeout_calibration import (  # noqa: E402
    apply_local_profile,
    assert_catalog_isolated,
    assert_isolated_storage,
    assess_provider_idle,
    attribute_deadline,
    cache_eligible,
    candidate_request_timeout,
    check_task_budget,
    derive_stage_proposal,
    effective_attempt_ceiling,
    effective_transport_timeout,
    load_legacy_result,
    plan_confirmation,
    quantile_linear,
    remaining_for_document,
    select_confirmation_docs,
    should_fallback_or_correct,
    should_retry_after_timeout,
    stage_attempt_timeout_ok,
    stage_censored_counts,
    summarize_durations,
    validate_measurement,
    write_measurements,
)


def test_timing_units_seconds_and_deadline_attribution():
    assert stage_attempt_timeout_ok(44.9, 45.0) is True
    assert stage_attempt_timeout_ok(45.1, 45.0) is False
    assert attribute_deadline(queue_wait_s=0.1, attempt_s=46.0,
                              request_timeout_s=45.0, stage_elapsed_s=50.0,
                              stage_limit_s=150.0) == "request"
    assert attribute_deadline(queue_wait_s=0.1, attempt_s=10.0,
                              request_timeout_s=45.0, stage_elapsed_s=200.0,
                              stage_limit_s=150.0) == "stage"
    with pytest.raises(ValueError):
        attribute_deadline(queue_wait_s=-1.0, attempt_s=1.0,
                           request_timeout_s=45.0, stage_elapsed_s=1.0,
                           stage_limit_s=10.0)


def test_whole_task_http_and_time_caps():
    assert check_task_budget(dispatches_used=59, elapsed_s=100.0) is None
    assert "http-cap" in (check_task_budget(dispatches_used=60, elapsed_s=100.0) or "")
    assert "time-cap" in (check_task_budget(dispatches_used=5, elapsed_s=3600.0) or "")
    # every attempt respects remaining document/task time
    assert remaining_for_document(doc_limit_s=600.0, task_remaining_s=30.0,
                                  request_timeout_s=300.0) == pytest.approx(30.0)


def test_no_retries_fallbacks_corrections_after_timeout():
    assert should_retry_after_timeout() is False
    assert should_retry_after_timeout({"timeout_retries": 0}) is False
    assert should_fallback_or_correct() is False
    assert should_fallback_or_correct({"fallbacks": 0, "corrections": 0}) is False


def test_censored_samples_never_merged_and_quantiles():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert quantile_linear(sorted(vals), 0.5) == pytest.approx(30.0)
    summary = summarize_durations(vals, censored_count=0, label="router")
    assert summary["n_completed"] == 5
    assert summary["provisional_tail"] is True  # n<20
    assert summary["p95"] is not None and summary["p95_withheld"] is False
    censored = summarize_durations(vals, censored_count=2, label="extractor")
    assert censored["n_censored"] == 2
    assert censored["p95"] is None and censored["p95_withheld"] is True
    empty = summarize_durations([], censored_count=3)
    assert empty["p95"] is None and empty["min"] is None
    with pytest.raises(ValueError):
        quantile_linear([], 0.5)


def test_storage_isolation_from_history_sources_catalogs_caches(tmp_path):
    ok = tmp_path / "bench" / "m.json"
    assert assert_isolated_storage(ok) == ok
    for protected in ("data/extraction.db", "data/sources/x.jpg",
                      "api/data/knowledge_base/field_catalog/invoice.json",
                      "data-local/cache/ocr-results/y"):
        with pytest.raises(ValueError):
            assert_isolated_storage(protected)
    with pytest.raises(ValueError):
        assert_catalog_isolated("/live/kb", "/live/kb")


def test_local_cloud_configuration_separation():
    current = {"LLM_PROVIDER": "ollama-local", "LLM_MODEL": "qwen2.5:3b",
               "LLM_REQUEST_TIMEOUT_SECONDS": 45, "LLM_MAX_CONCURRENT_REQUESTS": 1}
    updated = apply_local_profile(current, {"LLM_REQUEST_TIMEOUT_SECONDS": 120},
                                  provider="ollama-local")
    assert updated["LLM_REQUEST_TIMEOUT_SECONDS"] == 120
    assert updated["LLM_MODEL"] == "qwen2.5:3b"  # preserved
    with pytest.raises(ValueError):
        apply_local_profile(current, {"LLM_MODEL": "other"}, provider="ollama-local")
    with pytest.raises(ValueError):
        apply_local_profile(current, {"LLM_REQUEST_TIMEOUT_SECONDS": 1},
                            provider="openai")
    with pytest.raises(ValueError):
        apply_local_profile(current, {"UNKNOWN_KEY": 1}, provider="ollama-local")


def test_failure_cache_eligibility():
    assert cache_eligible("completed") is True
    for outcome in ("failed", "partial", "cancelled", "not_attempted"):
        assert cache_eligible(outcome) is False


def test_benchmark_output_and_legacy_loading(tmp_path):
    rec = {"source_id": "sroie_X1.jpg", "outcome": "completed", "dispatches": 3,
           "ocr_seconds": 1.0, "router_seconds": 2.0, "extractor_seconds": 3.0,
           "judge_seconds": 0.0, "total_seconds": 6.0}
    validate_measurement(rec)
    out = write_measurements(tmp_path / "bench" / "m.json", [rec])
    assert out.exists()
    bad = dict(rec)
    bad.pop("dispatches")
    with pytest.raises(ValueError):
        validate_measurement(bad)
    legacy = load_legacy_result({"documents": []})
    assert legacy["diagnostics"] == {} and legacy["dispatches"] == 0


def test_candidate_timeout_heuristic_withheld_when_censored():
    assert candidate_request_timeout(100.0) == pytest.approx(125.0)
    assert candidate_request_timeout(None) is None


def test_effective_attempt_ceiling_respects_remaining_doc_task_time():
    # Every attempt respects min(request, doc-remaining, task-remaining).
    assert effective_attempt_ceiling(
        request_timeout_s=300.0, doc_remaining_s=600.0,
        task_remaining_s=3600.0) == pytest.approx(300.0)
    assert effective_attempt_ceiling(
        request_timeout_s=300.0, doc_remaining_s=600.0,
        task_remaining_s=120.0) == pytest.approx(120.0)
    assert effective_attempt_ceiling(
        request_timeout_s=300.0, doc_remaining_s=40.0,
        task_remaining_s=3600.0) == pytest.approx(40.0)
    assert effective_attempt_ceiling(
        request_timeout_s=300.0, doc_remaining_s=0.0,
        task_remaining_s=3600.0) == pytest.approx(0.0)


def test_stage_censored_counts_are_per_stage():
    # Timeouts must never be lumped across stages: Router/Judge proposals
    # must not be poisoned by Extractor censoring.
    events = [("router", "ok"), ("extractor", "timeout"),
              ("extractor", "timeout"), ("judge", "ok")]
    counts = stage_censored_counts(events)
    assert counts == {"router": 0, "extractor": 2, "judge": 0}
    assert stage_censored_counts([]) == {}


def test_select_confirmation_docs_slowest_plus_predetermined():
    records = [
        {"source_id": "a.jpg", "outcome": "completed", "total_seconds": 50.0},
        {"source_id": "b.jpg", "outcome": "completed", "total_seconds": 120.0},
        {"source_id": "c.jpg", "outcome": "failed", "total_seconds": 300.0},
        {"source_id": "d.jpg", "outcome": "completed", "total_seconds": 60.0},
    ]
    picked = select_confirmation_docs(records, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    assert picked is not None
    assert picked["slowest"] == "b.jpg"
    # Predetermined comparison: first completed in deterministic order,
    # distinct from the slowest.
    assert picked["comparison"] == "a.jpg"
    assert select_confirmation_docs(
        [{"source_id": "a.jpg", "outcome": "completed", "total_seconds": 1.0}],
        ["a.jpg"]) is None
    assert select_confirmation_docs([], []) is None


def test_derive_stage_proposal_uses_only_own_stage_p95():
    good = derive_stage_proposal(
        p95_s=80.0, censored_count=0, label="router",
        current_request_s=45.0, current_stage_s=100.0)
    assert good["candidate_request_s"] == pytest.approx(100.0)
    assert good["withheld"] is False
    # Production single retry needs ~2x request + 2s inside the stage limit;
    # a no-retry measurement never validates retry timing by itself.
    assert "retry" in good["retry_fit"].lower() or "retry" in good["note"].lower()
    censored = derive_stage_proposal(
        p95_s=None, censored_count=2, label="extractor",
        current_request_s=45.0, current_stage_s=150.0)
    assert censored["candidate_request_s"] is None
    assert censored["withheld"] is True


def test_assess_provider_idle_ps_alone_never_proves_idle():
    # Clean doc, all dispatches terminal, no app jobs, provider reachable.
    decision, _reason = assess_provider_idle(
        active_jobs=[], had_timeout_or_cancel=False,
        ps_reachable_before=True, ps_reachable_after=True)
    assert decision == "proceed"
    # /api/ps reachable but a timeout occurred: server-side cancellation
    # is unprovable -> provisional at best, never established.
    decision, _reason = assess_provider_idle(
        active_jobs=[], had_timeout_or_cancel=True,
        ps_reachable_before=True, ps_reachable_after=True)
    assert decision == "proceed-provisional"
    # Active application jobs always stop the sweep.
    decision, _reason = assess_provider_idle(
        active_jobs=[("job1", "processing")], had_timeout_or_cancel=False,
        ps_reachable_before=True, ps_reachable_after=True)
    assert decision == "stop"
    # Unreachable provider after the doc: state unknown -> stop.
    decision, _reason = assess_provider_idle(
        active_jobs=[], had_timeout_or_cancel=False,
        ps_reachable_before=True, ps_reachable_after=False)
    assert decision == "stop"


def test_attempt_ceiling_defaults_off_and_bounds_transport():
    # Default-off: existing paths are byte-identical when unset.
    assert attempt_ceiling_s.get() is None
    assert effective_transport_timeout(300.0, None) == pytest.approx(300.0)
    assert effective_transport_timeout(300.0, 120.0) == pytest.approx(120.0)
    assert effective_transport_timeout(300.0, 0.0) == pytest.approx(0.0)


def test_plan_confirmation_shares_task_budget():
    records = [
        {"source_id": "a.jpg", "outcome": "completed", "total_seconds": 50.0},
        {"source_id": "b.jpg", "outcome": "completed", "total_seconds": 120.0},
        {"source_id": "c.jpg", "outcome": "completed", "total_seconds": 60.0},
    ]
    order = ["a.jpg", "b.jpg", "c.jpg"]
    # Budget remains: slowest + predetermined comparison.
    assert plan_confirmation(records, order, dispatches_used=10,
                             elapsed_s=1000.0) == ["b.jpg", "a.jpg"]
    # Exhausted dispatch budget: no confirmation docs (same 60-cap).
    assert plan_confirmation(records, order, dispatches_used=60,
                             elapsed_s=1000.0) == []
    # Exhausted time budget: no confirmation docs (same 60-min cap).
    assert plan_confirmation(records, order, dispatches_used=10,
                             elapsed_s=3600.0) == []
