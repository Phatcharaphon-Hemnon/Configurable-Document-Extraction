"""Opt-in region-request optimization (default off): offline regressions.

RED-first for REGION_COMPACT_REQUEST (default off), built on the measured
compact A/B (docs/reports/compact_request_ab_2026-09-16.md — both full-page
conditions timed out at 45s; compaction shrank server-counted prompt tokens
but quality/speedup remain unverified):

- Default-off: legacy region task text is byte-identical when disabled.
- Region-specific output contracts (header / table / totals / notes / page):
  role context preserved, complete row boundaries, full catalog names still
  allowed — never silently restricted to SROIE's scored fields.
- Coverage ledger preserved: every source block assigned / excluded (blank
  only) / unresolved; no fabricated geometry, no omitted content.
- Typed conversion into the existing contract preserves evidence, repeated
  columns, printed zeros, conflicts, and page isolation.
- Sequential finite budgets: accounting derives from actual requests; no
  output-capacity cuts, no deadline changes; an explicit finding surfaces
  when all regions cannot fit the existing job budget.
- Versioned fingerprints: region-request version + flag invalidate
  checkpoints and the completed-result cache.
- Offline size reporting: per-region serialized sizes + duplicated context
  (no latency claim — smaller requests do not imply faster runs).

No live inference; mocked providers; temp storage only.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.documents import (  # noqa: E402
    ExtractedField,
    ExtractedTable,
    ExtractionCallResult,
    TableCell,
    TableColumn,
)
from app.schemas.ocr import OCRBlock  # noqa: E402
from app.services.regions import (  # noqa: E402
    Region,
    build_region_task_text,
    fingerprint_region,
    merge_region_outputs,
    split_page,
)


def _region(kind="table", text="Item Qty\nWIDGET-A 2", bids=("a", "b")):
    return Region(
        id=f"page-1-region-{kind}-0", kind=kind, page_number=1,
        text=text, block_ids=list(bids),
        context_note="first line(s) are repeated column context, not data"
        if kind == "table" else None,
    )


# ------------------------------------------------------------------
# Flag defaults off
# ------------------------------------------------------------------

def test_region_request_flag_defaults_off():
    from app.services.region_requests import region_request_enabled

    assert region_request_enabled(SimpleNamespace()) is False
    assert region_request_enabled(
        SimpleNamespace(region_compact_request=False)) is False
    assert region_request_enabled(
        SimpleNamespace(region_compact_request=True)) is True
    # MagicMock settings (legacy doubles) must NOT enable implicitly.
    assert region_request_enabled(MagicMock()) is False


def test_region_request_version_pinned():
    from app.services.region_requests import REGION_REQUEST_VERSION

    assert isinstance(REGION_REQUEST_VERSION, str) and REGION_REQUEST_VERSION


# ------------------------------------------------------------------
# Region-specific output contracts
# ------------------------------------------------------------------

def test_kind_contracts_cover_all_splitter_kinds():
    from app.services.region_requests import REGION_KIND_CONTRACTS

    assert set(REGION_KIND_CONTRACTS) >= {"header", "table", "totals", "notes", "page"}


@pytest.mark.parametrize("kind", ["header", "table", "totals", "notes", "page"])
def test_kind_contract_preserves_role_context_and_full_catalog(kind):
    """Each contract keeps role context + evidence rules and explicitly
    allows full catalog names (never an SROIE-only field list)."""
    from app.services.region_requests import REGION_KIND_CONTRACTS

    contract = REGION_KIND_CONTRACTS[kind]
    assert "region" in contract.lower()
    assert "source_span" in contract
    assert "verbatim" in contract.lower()
    # Full catalog still allowed: must say so, must not enumerate an
    # SROIE-only allowlist as the permitted output.
    assert "catalog" in contract.lower()
    for scored in ("company", "date", "address", "total"):
        assert contract.lower().count(scored) <= 2, (
            f"{kind} contract must not read as an SROIE scored-field allowlist"
        )


def test_table_contract_demands_complete_rows():
    from app.services.region_requests import REGION_KIND_CONTRACTS

    contract = REGION_KIND_CONTRACTS["table"]
    assert "all" in contract.lower() and "row" in contract.lower()
    assert "order" in contract.lower()


def test_totals_contract_never_recalculates():
    from app.services.region_requests import REGION_KIND_CONTRACTS

    contract = REGION_KIND_CONTRACTS["totals"]
    assert "verbatim" in contract.lower()
    assert "recalculat" in contract.lower() or "never" in contract.lower()


def test_request_text_preserves_row_boundaries_and_context():
    from app.services.region_requests import build_region_request_text

    region = _region()
    text = build_region_request_text(region)
    # Complete row boundaries: the full region text travels verbatim.
    assert region.text in text
    # Necessary role context: kind + repeated-column context note preserved.
    assert "table" in text.lower()
    assert (region.context_note or "") in text
    # Evidence rule preserved from the legacy builder.
    assert "source_span" in text


def test_request_text_adds_no_geometry():
    """No fabricated geometry: the builder adds no coordinates/boxes."""
    from app.services.region_requests import build_region_request_text

    region = _region()
    text = build_region_request_text(region)
    for token in ("box", "bbox", "x=", "y=", "coordinates"):
        assert token not in text.lower()


def test_disabled_mode_is_byte_identical_to_legacy():
    from app.services.region_requests import build_region_request_text

    region = _region()
    assert build_region_request_text(region, enabled=False) == build_region_task_text(region)


# ------------------------------------------------------------------
# Coverage preservation (splitter reuse)
# ------------------------------------------------------------------

def test_every_source_block_accounted():
    blocks = [
        OCRBlock(text="ACME CO", confidence=0.9, box=(10, 10, 100, 12), block_id="h"),
        OCRBlock(text="Item", confidence=0.9, box=(10, 60, 60, 12), block_id="h1"),
        OCRBlock(text="Qty", confidence=0.9, box=(200, 60, 30, 12), block_id="h2"),
        OCRBlock(text="WIDGET-A", confidence=0.9, box=(10, 80, 60, 12), block_id="r1"),
        OCRBlock(text="2", confidence=0.9, box=(200, 80, 30, 12), block_id="q1"),
        OCRBlock(text="Total: 25.50", confidence=0.9, box=(10, 130, 80, 12), block_id="t"),
        OCRBlock(text="   ", confidence=0.0, box=(10, 150, 10, 10), block_id="blank"),
    ]
    text = "ACME CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"
    split = split_page(blocks, text, 1)
    accounted = set(split.coverage.assigned) | set(split.coverage.excluded) | set(
        split.coverage.unresolved)
    for block in blocks:
        assert block.block_id in accounted, f"{block.block_id} missing from ledger"
    # Blank text is the ONLY excludable reason.
    for bid, reason in split.coverage.excluded.items():
        assert reason == "blank", f"{bid} excluded as {reason!r}"


# ------------------------------------------------------------------
# Typed conversion into the existing contract
# ------------------------------------------------------------------

def _call_with_evidence():
    return ExtractionCallResult(
        doc_type="invoice", page_number=1,
        fields=[
            ExtractedField(name="total_amount", value=0.0,
                           confidence=0.9, source_span="Total: 0.00"),
            ExtractedField(name="invoice_number", value="INV-1",
                           confidence=0.8, source_span="INV-1"),
        ],
        tables=[ExtractedTable(
            name="line_items",
            columns=[TableColumn(key="product_name", label="Item"),
                     TableColumn(key="product_name", label="Item-dup")],
            rows=[[TableCell(column="product_name", value="WIDGET-A",
                             confidence=0.9, source_span="WIDGET-A"),
                   TableCell(column="product_name", value="WIDGET-A",
                             confidence=0.9, source_span="WIDGET-A")]],
        )],
        new_field_names=[],
    )


def test_typed_conversion_preserves_evidence_zeros_and_repeats():
    from app.services.region_requests import convert_region_call

    region = _region()
    call = _call_with_evidence()
    out = convert_region_call(region, call, page_number=1)
    assert out["region"] is region
    by_name = {f.name: f for f in out["fields"]}
    # Printed zero preserved (not dropped as falsy).
    assert by_name["total_amount"].value == 0.0
    assert by_name["total_amount"].source_span == "Total: 0.00"
    # Repeated columns preserved (no dedup of table rows/cells).
    assert len(out["tables"][0].rows[0]) == 2
    # Region provenance stamped without mutating the caller's objects.
    assert all(getattr(f, "_region_id", None) == region.id for f in out["fields"])


def test_typed_conversion_is_page_isolated():
    from app.services.region_requests import convert_region_call

    region = _region()
    call = _call_with_evidence()
    before = copy.deepcopy(call.model_dump(mode="json"))
    out = convert_region_call(region, call, page_number=1)
    out["tables"][0].rows[0][0].value = "MUTATED"
    out["fields"][0].value = "MUTATED"
    assert call.model_dump(mode="json") == before


def test_typed_conversion_rejects_cross_page_calls():
    from app.services.region_requests import convert_region_call

    region = _region()
    call = _call_with_evidence()
    with pytest.raises(ValueError, match="page"):
        convert_region_call(region, call, page_number=2)


def test_merge_keeps_conflicts_for_review():
    from app.services.region_requests import convert_region_call

    r1 = Region(id="page-1-region-header-0", kind="header", page_number=1,
                text="Total: 25.50", block_ids=["t"])
    r2 = Region(id="page-1-region-totals-1", kind="totals", page_number=1,
                text="Total: 26.50", block_ids=["t2"])
    c1 = ExtractionCallResult(
        doc_type="invoice", page_number=1,
        fields=[ExtractedField(name="total_amount", value=25.50,
                               confidence=0.9, source_span="Total: 25.50")],
        tables=[], new_field_names=[])
    c2 = ExtractionCallResult(
        doc_type="invoice", page_number=1,
        fields=[ExtractedField(name="total_amount", value=26.50,
                               confidence=0.9, source_span="Total: 26.50")],
        tables=[], new_field_names=[])
    merged = merge_region_outputs([
        convert_region_call(r1, c1, page_number=1),
        convert_region_call(r2, c2, page_number=1),
    ])
    assert len(merged["fields"]) == 1  # first occurrence kept
    assert len(merged["conflicts"]) == 1  # conflict retained for review


# ------------------------------------------------------------------
# Budgets: finite, sequential, derived from actual requests
# ------------------------------------------------------------------

def test_budget_estimate_flags_unfittable_region_sets():
    from app.services.region_requests import estimate_region_dispatch_budget

    ok = estimate_region_dispatch_budget(
        n_regions=2, page_used=0, job_used=0,
        page_cap=24, job_cap=64)
    assert ok["fits"] is True
    assert ok["needed_worst_case"] == 8  # 2 regions x shared 4-attempt budget
    tight = estimate_region_dispatch_budget(
        n_regions=10, page_used=0, job_used=0,
        page_cap=24, job_cap=64)
    assert tight["fits"] is False
    assert "budget" in tight["message"].lower()
    assert "24" in tight["message"]  # names the binding existing cap


def test_budget_never_raises_caps_or_deadlines():
    from app.services import regions as _regions
    from app.services.region_requests import estimate_region_dispatch_budget

    est = estimate_region_dispatch_budget(
        n_regions=100, page_used=0, job_used=0,
        page_cap=_regions.MAX_REGION_DISPATCHES_PER_PAGE,
        job_cap=_regions.MAX_REGION_DISPATCHES_PER_JOB)
    assert est["fits"] is False
    # The estimate surfaces the shortfall; caps are reported, never mutated.
    assert _regions.MAX_REGION_DISPATCHES_PER_PAGE == 24
    assert _regions.MAX_REGION_DISPATCHES_PER_JOB == 64


# ------------------------------------------------------------------
# Fingerprints: versioned, invalidating
# ------------------------------------------------------------------

def test_region_fingerprint_invalidated_by_request_version():
    from app.services.region_requests import REGION_REQUEST_VERSION

    region = _region()
    base = fingerprint_region(
        job_id="j", source_id="s", page_number=1, region=region,
        block_texts={"a": "x", "b": "y"}, upstream_text_sha="u",
        catalog_sha="c", prompt_version="p", model_fingerprint={})
    bumped = fingerprint_region(
        job_id="j", source_id="s", page_number=1, region=region,
        block_texts={"a": "x", "b": "y"}, upstream_text_sha="u",
        catalog_sha="c", prompt_version=f"p+{REGION_REQUEST_VERSION}",
        model_fingerprint={})
    assert base != bumped


def test_fingerprints_differ_by_region_request_flag(tmp_path):
    from app.services.result_cache import ResultCache

    def _settings(**overrides):
        s = SimpleNamespace(
            cache_path=str(tmp_path / "cache"),
            result_cache_ttl_seconds=60, result_cache_max_entries=8,
            result_cache_enabled=True, ocr_engine="tesseract",
            ocr_languages="eng+tha", ocr_dpi=300,
            llm_base_url="https://test.invalid/v1", router_model_name="m",
            extraction_model_name="m", judge_model_name="m",
            llm_temperature=0.0, extraction_max_tokens=3000,
            router_max_tokens=400, router_text_chars=2000,
            disable_strict_json_schema=False,
            few_shot_examples_per_doc_type=0, llm_reasoning_effort="",
            judge_skip_when_clean=True, judge_skip_confidence=0.85,
            knowledge_base_path=str(tmp_path / "kb"),
            extraction_compact_request=False, region_compact_request=False,
        )
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    fp_off = ResultCache(_settings()).config_fingerprint_dict()
    fp_on = ResultCache(
        _settings(region_compact_request=True)).config_fingerprint_dict()
    assert fp_off != fp_on
    assert fp_off["provider"]["region_compact_request"] is False
    assert fp_on["provider"]["region_compact_request"] is True
    assert fp_off["versions"]["region_request"] == fp_on["versions"]["region_request"]


def test_region_model_fingerprint_covers_flag_and_version(tmp_path):
    from app.core.config import Settings
    from app.services.extraction_service import DocumentExtractionService
    from app.services.region_requests import REGION_REQUEST_VERSION

    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [{"name": "invoice_number", "type": "string"}],
    }), encoding="utf-8")
    for name in ("po_fields", "delivery_note_fields"):
        (kb / "field_catalog" / f"{name}.json").write_text(json.dumps({
            "doc_type": "x", "fields": [{"name": "n", "type": "string"}],
        }), encoding="utf-8")
    # Real Settings with tmp-path overrides (a hand-enumerated namespace
    # rots whenever the service reads a new setting).
    s = Settings()
    s.knowledge_base_path = str(kb)
    s.database_enabled = True
    s.database_path = str(tmp_path / "h.db")
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.result_cache_enabled = False
    s.region_extraction_enabled = True
    s.region_compact_request = False
    service = DocumentExtractionService(settings=s)
    fp = service._region_model_fingerprint()
    assert fp["region_compact_request"] is False
    assert fp["region_request_version"] == REGION_REQUEST_VERSION


# ------------------------------------------------------------------
# Flag matrix: region path × region contracts × extraction compact
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    "region_enabled,region_compact,extraction_compact,expect_regions,expect_contract",
    [
        (False, False, False, False, False),  # default-off: full-page path
        (False, True, True, False, False),  # flags without the path: still full-page
        (True, False, False, True, False),  # path on, contracts off: legacy text
        (True, False, True, True, False),  # extraction compact never leaks into regions
        (True, True, False, True, True),  # contracts on
        (True, True, True, True, True),  # both compacts: region contracts unchanged
    ],
)
@pytest.mark.anyio
async def test_flag_matrix_region_path_and_contracts(
    tmp_path, region_enabled, region_compact, extraction_compact,
    expect_regions, expect_contract,
):
    """Every flag combination resolves deterministically: the region path
    runs only when enabled AND geometry splits; contracts appear only when
    opted in; the extraction-level compact flag never alters region text."""
    from app.schemas.documents import JudgeResult, RoutingDecision
    from app.schemas.ocr import OCRPage

    service = _service(
        tmp_path,
        region_extraction_enabled=region_enabled,
        region_compact_request=region_compact,
        extraction_compact_request=extraction_compact,
    )
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9,
                                     reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    seen_texts: list[str] = []

    async def _fake_extract_call(text, **kwargs):
        seen_texts.append(text)
        return ExtractionCallResult(
            doc_type="invoice", page_number=kwargs.get("page_number", 1),
            fields=[ExtractedField(name="invoice_number", value="INV-1",
                                   confidence=0.95,
                                   source_span="ACME REPLAY CO")],
            tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract_call = _fake_extract_call
    blocks = _multi_region_blocks()
    text = "ACME REPLAY CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"
    split = split_page(blocks, text, 1)
    if len(split.regions) < 2 and expect_regions:
        pytest.skip("geometry yields single region on this splitter version")
    ocr_page = OCRPage(text=text, blocks=blocks)
    service.ocr = MagicMock()
    service.ocr.aparse_file = AsyncMock(return_value=[text])
    service.ocr.last_pages = [ocr_page]
    service.ocr.model_hashes = {}
    service.ocr._hybrid_fingerprint = MagicMock(return_value={})
    job = service.job_store.create(
        filename="a.png", content_type="image/png", size_bytes=3)
    from app.services.extraction_service import UploadedFilePart

    await service.run_job(
        job.job_id, [UploadedFilePart("a.png", "image/png", b"img")])
    status = service.get_batch_status(job.job_id)
    assert status is not None
    # Legacy region texts start with "[Region …]"; the full-page prompt
    # never contains that marker.
    regions_ran = any("[Region " in sent for sent in seen_texts)
    if expect_regions:
        assert regions_ran, f"region path expected, texts={len(seen_texts)}"
    if expect_contract:
        assert any("region-request" in sent.lower() for sent in seen_texts)
    else:
        assert all("region-request" not in sent.lower() for sent in seen_texts)


# ------------------------------------------------------------------
# Semantic / evidence coverage: every kept value traces to region text
# ------------------------------------------------------------------

def test_merged_fields_traceable_to_their_region_text():
    """Semantic coverage: each merged field keeps the verbatim evidence from
    the region that produced it (span ⊆ region text); conflicts stay
    explicit instead of silently winning."""
    from app.services.region_requests import convert_region_call

    header = Region(id="page-1-region-header-0", kind="header", page_number=1,
                    text="ACME CO\nTAX INVOICE No: INV-7", block_ids=["h"])
    totals = Region(id="page-1-region-totals-1", kind="totals", page_number=1,
                    text="Subtotal: 20.00\nTotal: 25.50", block_ids=["t"])
    header_call = ExtractionCallResult(
        doc_type="invoice", page_number=1,
        fields=[ExtractedField(name="invoice_number", value="INV-7",
                               confidence=0.9,
                               source_span="TAX INVOICE No: INV-7")],
        tables=[], new_field_names=[])
    totals_call = ExtractionCallResult(
        doc_type="invoice", page_number=1,
        fields=[ExtractedField(name="total_amount", value=25.50,
                               confidence=0.9, source_span="Total: 25.50")],
        tables=[], new_field_names=[])
    merged = merge_region_outputs([
        convert_region_call(header, header_call, page_number=1),
        convert_region_call(totals, totals_call, page_number=1),
    ])
    assert {f.name for f in merged["fields"]} == {"invoice_number", "total_amount"}
    by_region = {header.id: header.text, totals.id: totals.text}
    for field in merged["fields"]:
        rid = getattr(field, "_region_id", None)
        assert rid in by_region
        assert field.source_span and field.source_span in by_region[rid], (
            f"{field.name} evidence not in its region text")
    assert merged["conflicts"] == []


def test_region_checkpoint_invalidated_by_request_version_bump():
    """Checkpoints embed the request version via the model fingerprint, so
    a contract bump recomputes regions instead of reusing stale outputs."""
    from app.services.region_requests import REGION_REQUEST_VERSION

    region = _region()
    fp_before = fingerprint_region(
        job_id="j", source_id="s", page_number=1, region=region,
        block_texts={"a": "x", "b": "y"}, upstream_text_sha="u",
        catalog_sha="c", prompt_version="p",
        model_fingerprint={"region_request_version": REGION_REQUEST_VERSION,
                           "region_compact_request": False})
    fp_after = fingerprint_region(
        job_id="j", source_id="s", page_number=1, region=region,
        block_texts={"a": "x", "b": "y"}, upstream_text_sha="u",
        catalog_sha="c", prompt_version="p",
        model_fingerprint={"region_request_version": "region-request-TEST",
                           "region_compact_request": False})
    assert fp_before != fp_after


def test_region_request_version_bump_invalidates_result_cache(tmp_path, monkeypatch):
    """A contract/logic bump must resolve old cache entries as misses."""
    import app.services.region_requests as rr_mod
    from app.services.result_cache import ResultCache

    def _settings(**overrides):
        s = SimpleNamespace(
            cache_path=str(tmp_path / "cache"),
            result_cache_ttl_seconds=60, result_cache_max_entries=8,
            result_cache_enabled=True, ocr_engine="tesseract",
            ocr_languages="eng+tha", ocr_dpi=300,
            llm_base_url="https://test.invalid/v1", router_model_name="m",
            extraction_model_name="m", judge_model_name="m",
            llm_temperature=0.0, extraction_max_tokens=3000,
            router_max_tokens=400, router_text_chars=2000,
            disable_strict_json_schema=False,
            few_shot_examples_per_doc_type=0, llm_reasoning_effort="",
            judge_skip_when_clean=True, judge_skip_confidence=0.85,
            knowledge_base_path=str(tmp_path / "kb"),
            extraction_compact_request=False, region_compact_request=False,
        )
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    before = ResultCache(_settings()).manifest_key(
        file_bytes=b"d", filename="a.png", page_count=1)
    monkeypatch.setattr(rr_mod, "REGION_REQUEST_VERSION", "region-request-TEST")
    after = ResultCache(_settings()).manifest_key(
        file_bytes=b"d", filename="a.png", page_count=1)
    assert before != after

def test_size_report_per_region_and_duplicated_context():
    from app.services.region_requests import summarize_region_requests

    regions = [_region("header", "ACME CO", ("h",)),
               _region("table", "Item Qty\nWIDGET-A 2", ("h1", "h2", "r1"))]
    texts = {r.id: build_region_task_text(r) for r in regions}
    report = summarize_region_requests(regions, texts)
    assert set(report["per_region_chars"]) == {r.id for r in regions}
    assert report["total_chars"] == sum(report["per_region_chars"].values())
    assert report["duplicated_context_chars"] >= 0
    # Smaller individual requests do not imply a smaller total: the sum
    # (with repeated context) is reported, never a speedup.
    assert "latency" not in json.dumps(report).lower()
    assert "speedup" not in json.dumps(report).lower()


# ------------------------------------------------------------------
# Service integration: default-off byte-identical, opt-in uses contracts
# ------------------------------------------------------------------

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
    service.ocr = MagicMock()
    return service


def _multi_region_blocks():
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


@pytest.mark.anyio
async def test_service_default_off_sends_legacy_task_text(tmp_path):
    from app.schemas.documents import RoutingDecision
    from app.schemas.ocr import OCRPage

    service = _service(tmp_path)
    assert service.settings.region_compact_request is False
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9,
                                     reason="t"))
    service.judge = MagicMock()
    from app.schemas.documents import JudgeResult
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    seen_texts: list[str] = []

    async def _fake_extract_call(text, **kwargs):
        seen_texts.append(text)
        return ExtractionCallResult(
            doc_type="invoice", page_number=kwargs.get("page_number", 1),
            fields=[ExtractedField(name="invoice_number", value="INV-1",
                                   confidence=0.95,
                                   source_span="ACME REPLAY CO")],
            tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract_call = _fake_extract_call
    blocks = _multi_region_blocks()
    text = "ACME REPLAY CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"
    split = split_page(blocks, text, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")
    ocr_page = OCRPage(text=text, blocks=blocks)
    outcome = await service._extract_page_with_regions(
        job_id=service.job_store.create(
            filename="a.png", content_type="image/png",
            size_bytes=3).job_id,
        filename="a.png", page_text=text, ocr_page=ocr_page,
        ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=False,
        job_started=0.0, source_key="a.png:abc",
    )
    assert outcome.document.error is None
    assert seen_texts, "region path must dispatch per-region calls"
    for sent in seen_texts:
        # Default-off: legacy builder output only (no kind-contract header).
        assert "region-request" not in sent.lower()


@pytest.mark.anyio
async def test_service_opt_in_sends_kind_contracts(tmp_path):
    from app.schemas.documents import JudgeResult, RoutingDecision
    from app.schemas.ocr import OCRPage

    service = _service(tmp_path, region_compact_request=True)
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9,
                                     reason="t"))
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok"))
    seen_texts: list[str] = []

    async def _fake_extract_call(text, **kwargs):
        seen_texts.append(text)
        return ExtractionCallResult(
            doc_type="invoice", page_number=kwargs.get("page_number", 1),
            fields=[ExtractedField(name="invoice_number", value="INV-1",
                                   confidence=0.95,
                                   source_span="ACME REPLAY CO")],
            tables=[], new_field_names=[])

    for ext in service.extractors.values():
        ext.extract_call = _fake_extract_call
    blocks = _multi_region_blocks()
    text = "ACME REPLAY CO\nItem Qty\nWIDGET-A 2\nTotal: 25.50"
    split = split_page(blocks, text, 1)
    if len(split.regions) < 2:
        pytest.skip("geometry yields single region on this splitter version")
    ocr_page = OCRPage(text=text, blocks=blocks)
    outcome = await service._extract_page_with_regions(
        job_id=service.job_store.create(
            filename="a.png", content_type="image/png",
            size_bytes=3).job_id,
        filename="a.png", page_text=text, ocr_page=ocr_page,
        ocr_uncertain_page=False, doc_type=None, page_index=1,
        progress={"dispatches": 0}, cache_on=False, region_cache=False,
        job_started=0.0, source_key="a.png:abc",
    )
    assert outcome.document.error is None
    assert seen_texts
    # Opt-in: every region request carries its kind contract; region bodies
    # still travel verbatim (no silently dropped content).
    assert any("region-request" in sent.lower() for sent in seen_texts)
    assert outcome.document.diagnostics.get("region_request_version")
