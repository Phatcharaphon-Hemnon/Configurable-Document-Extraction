"""Page isolation + typed results + Judge reconciliation (synthetic)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.agents.judge import reconcile_judge_issues  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.schemas.documents import (  # noqa: E402
    ExtractedField,
    ExtractionCallResult,
    JudgeIssue,
    JudgeResult,
    RoutingDecision,
)
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402


def _settings(tmp_path: Path) -> Settings:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True, exist_ok=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    s = Settings()
    s.knowledge_base_path = str(kb)
    s.database_enabled = False
    return s


@pytest.mark.asyncio
async def test_extractor_returns_typed_call_without_shared_state(tmp_path):
    from app.agents.extractors import build_extractors

    s = _settings(tmp_path)
    from app.services.field_catalog import FieldCatalog

    cat = FieldCatalog(Path(s.knowledge_base_path))
    extractors = build_extractors(s, cat)
    ext = extractors["invoice"]
    assert not hasattr(ext, "last_tables")
    parsed = MagicMock()
    parsed.fields = [MagicMock(name="x")]
    # Build a minimal parsed double via the real schema instead.
    from app.schemas.llm_schemas import ExtractedFieldEntry, ExtractionResponseSchema

    schema = ExtractionResponseSchema(
        fields=[ExtractedFieldEntry(name="invoice_number", value="INV-1", confidence=0.9,
                                    source_span="No: INV-1")],
        tables=[],
    )
    ext._client = MagicMock()
    ext._client.generate_structured = AsyncMock(return_value=MagicMock(
        parsed=schema, prompt_tokens=1, completion_tokens=1))
    call = await ext.extract_call(text="No: INV-1", page_number=2)
    assert isinstance(call, ExtractionCallResult)
    assert call.page_number == 2 and call.doc_type == "invoice"
    assert [f.name for f in call.fields] == ["invoice_number"]
    # Backward-compat tuple path still works.
    ext2 = build_extractors(s, cat)["invoice"]
    ext2._client = ext._client
    fields, _ = await ext2.extract(text="No: INV-1")
    assert [f.name for f in fields] == ["invoice_number"]


@pytest.mark.asyncio
async def test_three_pages_isolated_no_leakage(tmp_path):
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.ocr = MagicMock()
    texts = ["No: INV-1 Total: 1", "No: INV-2 Total: 2", "No: INV-3 Total: 3"]
    service.ocr.aparse_file = AsyncMock(return_value=texts)
    from app.schemas.ocr import OCRPage

    service.ocr.last_pages = [OCRPage(text=t) for t in texts]
    service.router = MagicMock()
    service.router.classify = AsyncMock(
        return_value=RoutingDecision(doc_type="invoice", confidence=0.9, reason="t"))

    async def _fake_extract_call(text="", few_shot=None, page_number=1, **kw):
        marker = f"INV-{page_number}"
        return ExtractionCallResult(
            doc_type="invoice", page_number=page_number,
            fields=[
                ExtractedField(name="invoice_number", value=marker, confidence=0.95,
                               source_span=f"No: {marker}"),
                ExtractedField(name="total_amount", value=float(page_number), confidence=0.9,
                               source_span=f"Total: {page_number}"),
            ],
            tables=[{
                "name": "line_items",
                "columns": [{"key": f"col_p{page_number}", "label": f"C{page_number}"}],
                "rows": [[{"column": f"col_p{page_number}", "value": marker,
                           "confidence": 0.9, "source_span": f"No: {marker}"}]],
            }],
            new_field_names=[],
        )

    for ext in service.extractors.values():
        ext.extract_call = AsyncMock(side_effect=_fake_extract_call)
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(return_value=JudgeResult(score=0.9, issues=[], notes="ok"))

    # Fix page texts so each marker resolves on its own page.
    service.ocr.aparse_file = AsyncMock(return_value=[
        "No: INV-1 Total: 1", "No: INV-2 Total: 2", "No: INV-3 Total: 3"])
    service.ocr.last_pages = [OCRPage(text=t) for t in [
        "No: INV-1 Total: 1", "No: INV-2 Total: 2", "No: INV-3 Total: 3"]]
    resp = await service.extract_group([UploadedFilePart("three.pdf", "application/pdf", b"pdf")])
    assert len(resp.documents) == 3
    markers = [d.fields[0].value for d in resp.documents]
    assert markers == ["INV-1", "INV-2", "INV-3"]
    cols = [[c.key for c in d.tables[0].columns] for d in resp.documents]
    assert cols == [["col_p1"], ["col_p2"], ["col_p3"]]
    # Temporal path uses the same typed call.
    from app.temporal import activities

    activities._page_service = service
    try:
        out = await activities.process_page_activity("three.pdf", "No: INV-1 Total: 1", [], page_number=1)
    finally:
        activities._page_service = None
    assert out["fields"][0]["value"] == "INV-1"


def test_judge_reconciliation_keeps_semantic_discards_false_mechanical():
    fields = [ExtractedField(name="buyer_name", value="Paula Parente", confidence=0.9,
                             source_span="Customer Name: Paula Parente")]
    page = "Customer Name: Paula Parente"
    false_mech = JudgeIssue(field="buyer_name", message="The value 'Paula Parente' is not found in the source text.",
                            severity="error", category="mechanical", target="field:buyer_name")
    kept, discarded = reconcile_judge_issues([false_mech], fields=fields, tables=[], page_text=page)
    assert kept == [] and len(discarded) == 1

    semantic = JudgeIssue(field="buyer_name", message="Paula Parente appears but role evidence shows customer, not supplier context.",
                          severity="warning", category="semantic", target="field:buyer_name")
    kept2, _ = reconcile_judge_issues([semantic], fields=fields, tables=[], page_text=page)
    assert len(kept2) == 1  # string presence never disproves role concerns

    dup = JudgeIssue(field="buyer_name", message="The value 'Paula Parente' is not found in the source text.",
                     severity="error", category="mechanical", target="field:buyer_name")
    kept3, discarded3 = reconcile_judge_issues([false_mech, dup], fields=fields, tables=[], page_text=page)
    assert kept3 == [] and len(discarded3) == 2


def test_skip_gate_requires_more_than_confidence(tmp_path):
    from app.agents.judge import should_skip_judge

    fields = [ExtractedField(name="invoice_number", value="INV-1", confidence=0.99, source_span="No: INV-1")]
    ok, _ = should_skip_judge(accepted_fields=fields, accepted_tables=[], required_coverage=1.0,
                              validation_errors=[], rejected_warning_or_error=False,
                              ocr_uncertain=False, min_confidence=0.99,
                              confidence_threshold=0.85, numeric_verbatim=True)
    assert ok is True
    for kwargs in (
        {"validation_errors": ["x"]},
        {"rejected_warning_or_error": True},
        {"required_coverage": 0.5},
        {"ocr_uncertain": True},
        {"min_confidence": 0.5},
        {"numeric_verbatim": False},
    ):
        base = dict(accepted_fields=fields, accepted_tables=[], required_coverage=1.0,
                    validation_errors=[], rejected_warning_or_error=False,
                    ocr_uncertain=False, min_confidence=0.99,
                    confidence_threshold=0.85, numeric_verbatim=True)
        base.update(kwargs)
        ok, _ = should_skip_judge(**base)
        assert ok is False
