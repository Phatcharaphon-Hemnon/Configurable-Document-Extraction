"""Schema contract tests: the exact shapes from the project spec."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.schemas.documents import (  # noqa: E402
    DOC_TYPES,
    ExtractedField,
    ExtractionResult,
)


def test_doc_type_is_fixed_to_three_types():
    assert set(DOC_TYPES) == {"invoice", "purchase_order", "delivery_note"}
    for doc_type in DOC_TYPES:
        ExtractionResult(doc_type=doc_type)
    with pytest.raises(ValidationError):
        ExtractionResult(doc_type="medical_form")  # type: ignore[arg-type]


def test_extracted_field_value_union():
    field = ExtractedField(name="total_amount", value=150.5, confidence=0.9, source_span="Total: 150.50")
    assert field.value == 150.5
    assert ExtractedField(name="invoice_date", value=date(2024, 5, 1), confidence=0.8).value == date(2024, 5, 1)
    with pytest.raises(ValidationError):
        ExtractedField(name="x", value=150.5, confidence=2.0)  # confidence out of range


def test_extraction_result_defaults():
    result = ExtractionResult(doc_type="invoice")
    assert result.fields == []
    assert result.validation_errors == []
    assert result.needs_review is False
