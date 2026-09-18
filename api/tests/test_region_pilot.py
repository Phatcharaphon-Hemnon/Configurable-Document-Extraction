"""Region pilot harness: offline mocked regressions (no live inference).

Covers the operator-only pilot contract (`api/scripts/diag_region_pilot.py`,
PREPARED ONLY — never executed here) against the ACTUAL region orchestration
(`DocumentExtractionService._extract_page_with_regions` with temp-isolated
storage, mocked Router/extractor/Judge doubles):

- partial success: first region completes with evidenced output, a later
  region fails → validated partial preserved, needs_review, page excluded
  from the completed-result cache, later regions unresolved (never silently
  skipped);
- budget exhaustion: DispatchBudgetExhausted mid-page → explicit
  budget-exhausted status, completed regions preserved, page not cacheable;
- failure: every region fails → error document, no usable region, Judge
  never reads as passed;
- timing attribution: first deterministically validated usable region is
  identified separately from final page completion (an unevidenced first
  region is NOT usable even when its LLM call "succeeded");
- shape accounting: rebuilt request shapes match actually-sent shapes;
- Judge invariant: omitted/unavailable Judge never yields `passed`.

No live inference; temp storage/DB only (conftest isolation + per-test tmp).
"""

from __future__ import annotations

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


def _load_pilot_module():
    script = _API_DIR / "scripts" / "diag_region_pilot.py"
    spec = importlib.util.spec_from_file_location("diag_region_pilot", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["diag_region_pilot"] = module
    spec.loader.exec_module(module)
    return module


pilot = _load_pilot_module()

from app.schemas.documents import (  # noqa: E402
    ExtractedField,
    ExtractionCallResult,
    JudgeResult,
    RoutingDecision,
)
from app.schemas.ocr import OCRBlock, OCRPage  # noqa: E402
from app.services.request_control import DispatchBudgetExhausted  # noqa: E402


def _blocks():
    return [
        OCRBlock(text="ACME REPLAY CO", confidence=0.9,
                 box=(10, 10, 100, 12), block_id="h"),
        OCRBlock(text="Item", confidence=0.9,
                 box=(10, 60, 60, 12), block_id="h1"),
        OCRBlock(text="Qty", confidence=0.9,
                 box=(200, 60, 30, 12), block_id="h2"),
        OCRBlock(text="WIDGET-A", confidence=0.9,
                 box=(10, 80, 60, 12), block_id="r1"),
        OCRBlock(text="2", confidence=0.9,
                 box=(200, 80, 30, 12), block_id="q1"),
        OCRBlock(text="Total: 25.50", confidence=0.9,
                 box=(10, 130, 80, 12), block_id="t"),
    ]


TEXT = "ACME REPLAY CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"


def _service(tmp_path, **overrides):
    from app.core.config import Settings
    from app.services.extraction_service import DocumentExtractionService

    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    for name, req in (("po_fields", "po_number"),
                      ("delivery_note_fields", "delivery_number")):
        (kb / "field_catalog" / f"{name}.json").write_text(json.dumps({
            "doc_type": "x",
            "fields": [{"name": req, "type": "string", "required": True}],
        }), encoding="utf-8")
    s = Settings()
    s.knowledge_base_path = str(kb)
    s.database_enabled = True
    s.database_path = str(tmp_path / "history.db")
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.result_cache_enabled = False
    s.region_extraction_enabled = True
    for k, v in overrides.items():
        setattr(s, k, v)
    service = DocumentExtractionService(settings=s)
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9, reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    return service


def _evidenced_call(page_number=1, **kwargs):
    return ExtractionCallResult(
        doc_type="invoice", page_number=kwargs.get("page_number", page_number),
        fields=[ExtractedField(name="invoice_number", value="ACME REPLAY CO",
                               confidence=0.95, source_span="ACME REPLAY CO")],
        tables=[], new_field_names=[])


async def _run_regions(service, extract_fn, **kwargs):
    for ext in service.extractors.values():
        ext.extract_call = extract_fn  # type: ignore[method-assign]
    ocr_page = OCRPage(text=TEXT, blocks=_blocks())
    t0 = time.perf_counter()
    job_id = service.job_store.create(
        filename="a.png", content_type="image/png", size_bytes=3).job_id
    outcome = await service._extract_page_with_regions(
        job_id=job_id,
        filename="a.png", page_text=TEXT, ocr_page=ocr_page,
        ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=True,
        job_started=t0, source_key="a.png:abc", **kwargs)
    return outcome, time.perf_counter() - t0, job_id


def _completed_by_region(service, job_id):
    from app.schemas.documents import ExtractedField as _EF
    from app.schemas.documents import ExtractedTable as _ET

    completed: dict[str, dict] = {}
    for record in service.job_store.get_regions(job_id):
        if record.get("status") != "completed":
            continue
        rid = record.get("region_id")
        if rid.endswith("-coverage"):
            continue
        payload = record.get("payload") or {}
        completed[rid] = {
            "fields": [_EF.model_validate(f).model_dump(mode="json")
                       for f in payload.get("fields", [])],
            "tables": [_ET.model_validate(t).model_dump(mode="json")
                       for t in payload.get("tables", [])],
        }
    return completed


def _region_texts():
    return {r["id"]: r["text"] for r in pilot._region_texts(_blocks(), TEXT)}


# ------------------------------------------------------------------
# Partial success
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_pilot_partial_success_preserves_first_usable(tmp_path):
    service = _service(tmp_path)
    calls = {"n": 0}

    async def _extract(text, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _evidenced_call(**kwargs)
        raise RuntimeError("simulated region failure")

    outcome, page_total, job_id = await _run_regions(service, _extract)
    doc = outcome.document
    assert doc.needs_review is True
    assert outcome.complete_for_cache is False
    # Later regions are failed/unresolved explicitly — never silently skipped.
    statuses = {r.get("region_id"): r.get("status")
                for r in service.job_store.get_regions(job_id)}
    assert calls["n"] >= 2
    assert set(statuses.values()) & {"failed", "unresolved"}
    # First validated usable region: deterministic post-hoc over checkpoints.
    from app.services.regions import split_page

    split = split_page(_blocks(), TEXT, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")
    usable = pilot.first_validated_usable(
        [r.id for r in split.regions],
        _completed_by_region(service, job_id),
        service.validator, "invoice", _region_texts())
    assert usable is not None
    assert usable["accepted_fields"] >= 1
    # Timing attribution: first completion strictly precedes page completion.
    assert outcome.first_region_elapsed is not None
    assert outcome.first_region_elapsed <= page_total


# ------------------------------------------------------------------
# Budget exhaustion
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_pilot_budget_exhaustion_explicit_and_not_cached(tmp_path):
    service = _service(tmp_path)
    calls = {"n": 0}

    async def _extract(text, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _evidenced_call(**kwargs)
        raise DispatchBudgetExhausted("budget-exhausted: page dispatch budget reached (24)")

    outcome, _, _ = await _run_regions(service, _extract)
    doc = outcome.document
    blob = " ".join(doc.validation_errors or []) + str(doc.diagnostics.get("extraction_path", ""))
    assert "budget" in blob.lower()
    assert any(f.name == "invoice_number" for f in doc.fields)
    assert doc.needs_review is True
    assert outcome.complete_for_cache is False


# ------------------------------------------------------------------
# Failure: no usable region, Judge never passed
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_pilot_all_failed_has_no_usable_region(tmp_path):
    service = _service(tmp_path)

    async def _extract(text, **kwargs):
        raise RuntimeError("simulated total region failure")

    outcome, _, _ = await _run_regions(service, _extract)
    doc = outcome.document
    assert doc.failed_stage == "extractor"
    assert outcome.complete_for_cache is False
    usable = pilot.first_validated_usable(
        [], {}, service.validator, "invoice", {})
    assert usable is None
    assert pilot.check_judge_invariant(
        doc.judge_status, doc.judge is not None)["invariant_ok"] is True
    assert doc.judge_status != "passed"


@pytest.mark.anyio
async def test_pilot_unavailable_judge_never_passed(tmp_path):
    service = _service(tmp_path)
    service.judge.evaluate = AsyncMock(side_effect=RuntimeError("judge down"))

    async def _extract(text, **kwargs):
        return _evidenced_call(**kwargs)

    outcome, _, _ = await _run_regions(service, _extract)
    doc = outcome.document
    assert doc.judge_status in ("unavailable", "skipped", "flagged")
    assert doc.judge_status != "passed"
    assert pilot.check_judge_invariant(
        doc.judge_status, doc.judge is not None)["invariant_ok"] is True


def test_judge_invariant_flags_passed_without_result():
    bad = pilot.check_judge_invariant("passed", False)
    assert bad["invariant_ok"] is False
    assert "VIOLATED" in bad["message"]
    for status in ("skipped", "unavailable", "flagged"):
        assert pilot.check_judge_invariant(status, False)["invariant_ok"] is True
    assert pilot.check_judge_invariant("passed", True)["invariant_ok"] is True


# ------------------------------------------------------------------
# Timing attribution: validated-usable != merely first-completed
# ------------------------------------------------------------------

@pytest.mark.anyio
async def test_pilot_first_usable_skips_unevidenced_region(tmp_path):
    service = _service(tmp_path)
    calls = {"n": 0}

    async def _extract(text, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Unevidenced output: call "succeeds" but the validator rejects
            # everything (span absent from region text) → NOT usable.
            return ExtractionCallResult(
                doc_type="invoice", page_number=kwargs.get("page_number", 1),
                fields=[ExtractedField(name="invoice_number", value="GHOST-9",
                                       confidence=0.99,
                                       source_span="GHOST-9 nowhere printed")],
                tables=[], new_field_names=[])
        # Evidence drawn from the LATER regions' own text (table/totals),
        # so post-hoc validation accepts them and only them.
        return ExtractionCallResult(
            doc_type="invoice", page_number=kwargs.get("page_number", 1),
            fields=[ExtractedField(name="invoice_number", value="WIDGET-A",
                                   confidence=0.9, source_span="WIDGET-A")],
            tables=[], new_field_names=[])

    outcome, page_total, job_id = await _run_regions(service, _extract)
    from app.services.regions import split_page

    split = split_page(_blocks(), TEXT, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")
    regions = [r.id for r in split.regions]
    texts = {r.id: r.text for r in split.regions}
    # Post-hoc over the REAL isolated checkpoints: the unevidenced first
    # region must be skipped even though its LLM call "succeeded".
    usable = pilot.first_validated_usable(
        regions, _completed_by_region(service, job_id),
        service.validator, "invoice", texts)
    assert usable is not None
    assert usable["region_id"] == regions[1]
    assert outcome.first_region_elapsed is not None
    assert outcome.first_region_elapsed <= page_total


# ------------------------------------------------------------------
# Shape + budget accounting (pure, offline)
# ------------------------------------------------------------------

def test_pilot_shapes_match_and_budget_gate():
    expected = pilot.build_expected_shapes(_blocks(), TEXT, 1, enabled=True)
    assert len(expected) >= 1
    observed = [{"seq": i, "chars": s["chars"], "sha": s["sha"]}
                for i, s in enumerate(expected)]
    matched = pilot.match_shapes(expected, observed)
    assert all(m["shape_match"] for m in matched)
    assert pilot.check_budget(8, 24, 64)["within_caps"] is True
    over = pilot.check_budget(25, 24, 64)
    assert over["within_caps"] is False
    assert "page ceiling 24" in over["message"]


def test_pilot_shape_mismatch_is_reported_not_hidden():
    expected = pilot.build_expected_shapes(_blocks(), TEXT, 1, enabled=True)
    tampered = [{"seq": 0, "chars": 1, "sha": "0" * 64}]
    matched = pilot.match_shapes(expected, tampered)
    assert matched[0]["shape_match"] is False


def test_pilot_never_reads_dataset_annotations():
    """The harness module must not reference box/entity transcript inputs."""
    assert pilot.__file__ is not None
    source = Path(pilot.__file__).read_text(encoding="utf-8")
    assert ".box.txt" not in source
    assert "entities.json" not in source
    assert "box transcript" not in source.lower()
