"""Bug 2 regression: non-business documents get an honest unsupported outcome.

Grade-table failure mode (funsd_0001118259.png): the router forced INVOICE
onto an "Allowable Grade Substitutions" table. The router must be able to
return doc_type="unsupported", the service must short-circuit before any
extractor runs, and low-confidence classifications must not extract on a
guess. The 3 fixed output types stay untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.agents.extractors import build_extractors  # noqa: E402
from app.agents.router import _ROUTER_PROMPT  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.schemas.documents import (  # noqa: E402
    DOC_TYPES,
    ROUTER_LOW_CONFIDENCE_THRESHOLD,
    UNSUPPORTED_DOCUMENT_MARKER,
    ExtractedField,
    ExtractionResult,
    JudgeResult,
    RoutingDecision,
)
from app.schemas.llm_schemas import RoutingResponseSchema  # noqa: E402
from app.services.extraction_service import (  # noqa: E402
    DocumentExtractionService,
    parse_doc_type,
)

# Mock OCR text shaped like the grade-substitution table: coherent English
# with no monetary business content.
GRADE_TABLE_TEXT = (
    "Allowable Grade Substitutions for Export CPCL\n"
    "Grade A may substitute for Grade B in export shipments\n"
    "Grade B may substitute for Grade C where approved\n"
    "All substitutions require inspector sign off before loading\n"
    "Reference sheet revised January 2024 for port authority use\n"
    "Contact the grading office for exceptions and appeals process\n"
)


def _settings(tmp_path: Path) -> Settings:
    kb = tmp_path / "kb"
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(json.dumps({
        "doc_type": "invoice",
        "fields": [
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "total_amount", "type": "number", "required": True},
        ],
    }), encoding="utf-8")
    s = Settings()
    s.knowledge_base_path = str(kb)
    return s


def _service(tmp_path: Path, routing: RoutingDecision):
    service = DocumentExtractionService(settings=_settings(tmp_path))
    service.router = MagicMock()
    service.router.classify = AsyncMock(return_value=routing)
    extractors_called: list[str] = []

    async def _fail_if_called(text: str):
        raise AssertionError("extractor must not run on short-circuited pages")

    for key, extractor in service.extractors.items():
        extractor.extract = AsyncMock(side_effect=_fail_if_called)
        extractors_called.append(key)
    service.judge = MagicMock()
    service.judge.evaluate = AsyncMock(
        return_value=JudgeResult(score=0.9, issues=[], notes="ok")
    )
    return service


def test_routing_schema_accepts_unsupported():
    parsed = RoutingResponseSchema(
        doc_type="unsupported", language="en", confidence=0.9,
        reason="grade substitution table, no monetary content",
    )
    assert parsed.doc_type == "unsupported"


def test_routing_schema_still_rejects_unknown_types():
    with pytest.raises(ValidationError):
        RoutingResponseSchema(doc_type="receipt", confidence=0.5)


def test_fixed_output_types_unchanged():
    assert tuple(DOC_TYPES) == ("invoice", "purchase_order", "delivery_note")
    with pytest.raises(ValidationError):
        RoutingDecision(doc_type="receipt", confidence=0.5)
    with pytest.raises(ValidationError):
        ExtractionResult(doc_type="unsupported", fields=[])
    service_keys = set(build_extractors(Settings(), MagicMock()).keys())
    assert service_keys == set(DOC_TYPES)


def test_router_prompt_names_unsupported_with_examples():
    assert "unsupported" in _ROUTER_PROMPT
    assert "Grade Substitutions" in _ROUTER_PROMPT


def test_parse_doc_type_rejects_unsupported_as_explicit_selection():
    with pytest.raises(ValueError):
        parse_doc_type("unsupported")


@pytest.mark.asyncio
async def test_unsupported_short_circuits_before_extractor(tmp_path):
    service = _service(
        tmp_path,
        RoutingDecision(doc_type="unsupported", language="en",
                        confidence=0.9, reason="grade table"),
    )
    doc = await service._extract_one_page(
        filename="funsd_0001118259.png", page_text=GRADE_TABLE_TEXT, page_number=1,
    )
    assert UNSUPPORTED_DOCUMENT_MARKER in (doc.error or "")
    assert any(UNSUPPORTED_DOCUMENT_MARKER in e for e in doc.validation_errors)
    assert doc.failed_stage == "router"
    assert doc.needs_review is True
    assert doc.fields == []
    assert doc.completeness_score == 0.0
    for extractor in service.extractors.values():
        extractor.extract.assert_not_called()
    service.judge.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_low_confidence_short_circuits_with_review(tmp_path):
    service = _service(
        tmp_path,
        RoutingDecision(doc_type="invoice", language="en",
                        confidence=ROUTER_LOW_CONFIDENCE_THRESHOLD - 0.2,
                        reason="guess"),
    )
    doc = await service._extract_one_page(
        filename="scan.png", page_text=GRADE_TABLE_TEXT, page_number=1,
    )
    assert doc.error is None and doc.failed_stage is None
    assert doc.doc_type == "invoice"
    assert doc.needs_review is True
    assert any("uncertain" in e for e in doc.validation_errors)
    assert doc.completeness_score == 0.0
    for extractor in service.extractors.values():
        extractor.extract.assert_not_called()


@pytest.mark.asyncio
async def test_confident_supported_routing_still_extracts(tmp_path):
    service = _service(
        tmp_path,
        RoutingDecision(doc_type="invoice", language="en",
                        confidence=0.95, reason="invoice layout"),
    )
    text = "Invoice No: INV-001 Total: 100 sandwich shop receipt counter sale"
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [
                ExtractedField(name="invoice_number", value="INV-001",
                               confidence=0.95, source_span="Invoice No: INV-001"),
                ExtractedField(name="total_amount", value=100.0,
                               confidence=0.9, source_span="Total: 100"),
            ],
            [],
        ))
    doc = await service._extract_one_page(
        filename="scan.png", page_text=text, page_number=1,
    )
    assert doc.failed_stage is None
    assert UNSUPPORTED_DOCUMENT_MARKER not in (doc.error or "")
    assert [f.name for f in doc.fields] == ["invoice_number", "total_amount"]


LATIN_RECEIPT_TEXT = (
    "RESTORAN WAN SHENG 002043319-W No 2 Jalan Temenggung "
    "Tax Invoice INV No 1126679 Date 05-05-2018 Cashier Nicole "
    "Teh Tarik 2 x 3.00 6.00 Total RM 19.61 Cash 20.00"
)


def _latin_extraction():
    return (
        [
            ExtractedField(name="invoice_number", value="1126679",
                           confidence=0.95, source_span="INV No 1126679"),
            ExtractedField(name="total_amount", value=19.61,
                           confidence=0.9, source_span="Total RM 19.61"),
        ],
        [],
    )


@pytest.mark.asyncio
async def test_th_tag_corrected_on_latin_page(tmp_path):
    service = _service(
        tmp_path,
        RoutingDecision(doc_type="invoice", language="th",
                        confidence=0.9, reason="invoice layout"),
    )
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=_latin_extraction())
    doc = await service._extract_one_page(
        filename="X51008042779.jpg", page_text=LATIN_RECEIPT_TEXT, page_number=1,
    )
    assert doc.language == "en"
    assert doc.routing_reason is not None and "th->en" in doc.routing_reason
    assert [f.name for f in doc.fields] == ["invoice_number", "total_amount"]


@pytest.mark.asyncio
async def test_th_tag_kept_on_thai_page(tmp_path):
    service = _service(
        tmp_path,
        RoutingDecision(doc_type="invoice", language="th",
                        confidence=0.9, reason="thai invoice"),
    )
    thai_text = ("ใบกำกับภาษี เลขที่ 44 บริษัท ตัวอย่าง จำกัด "
                 "ข้าว 2 50 น้ำ 1 20 รวม 70 บาท")
    for extractor in service.extractors.values():
        extractor.extract = AsyncMock(return_value=(
            [ExtractedField(name="invoice_number", value="44",
                            confidence=0.9, source_span="เลขที่ 44")],
            [],
        ))
    doc = await service._extract_one_page(
        filename="thai_bill.jpg", page_text=thai_text, page_number=1,
    )
    assert doc.language == "th"
    assert "th->en" not in (doc.routing_reason or "")
