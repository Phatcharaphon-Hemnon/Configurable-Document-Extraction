from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.schemas.documents import EvaluateResponse
from app.services.extraction_service import DocumentExtractionService


def _service() -> DocumentExtractionService:
    settings = MagicMock()
    settings.knowledge_base_path = str(Path(__file__).resolve().parents[2] / "Backend" / "app" / "data" / "knowledge_base")
    settings.few_shot_examples_per_doc_type = 0
    settings.recommended_extraction_model_name = "mock"
    return DocumentExtractionService(settings=settings)


def _eval(svc: DocumentExtractionService, prediction: dict[str, Any], gt: dict[str, Any]) -> EvaluateResponse:
    return svc.evaluate(prediction=prediction, ground_truth=gt)


def test_exact_match_fast_path() -> None:
    svc = _service()
    r = _eval(svc, {"a": 1}, {"a": 1})
    assert r.f1 == pytest.approx(1.0)


def test_none_handling() -> None:
    svc = _service()
    assert _eval(svc, {"a": None}, {"a": None}).f1 == pytest.approx(1.0)
    assert _eval(svc, {"a": ""}, {"a": None}).f1 == pytest.approx(0.0)


def test_numeric_like_match_with_commas_and_decimals() -> None:
    svc = _service()
    pred = {"total": 7570.0}
    gt = {"total": "7,570.00"}
    assert _eval(svc, pred, gt).f1 == pytest.approx(1.0)


def test_string_case_insensitive_and_whitespace_normalized() -> None:
    svc = _service()
    pred = {"name": "  Bangkok   Print & Packaging  "}
    gt = {"name": "bangkok print & packaging"}
    assert _eval(svc, pred, gt).f1 == pytest.approx(1.0)


def test_date_like_match() -> None:
    svc = _service()
    pred = {"statement_date": "10/9/2023"}
    gt = {"statement_date": "10/9/2023"}
    assert _eval(svc, pred, gt).f1 == pytest.approx(1.0)


def test_date_like_falls_back_when_only_one_side_parses() -> None:
    svc = _service()
    pred = {"statement_date": "10/9/2023"}
    gt = {"statement_date": "not-a-date"}
    assert _eval(svc, pred, gt).f1 == pytest.approx(0.0)


def test_list_json_encoded_string_vs_list() -> None:
    svc = _service()
    pred = {"line_items": [{"amount": 10, "qty": 2}]}
    gt = {"line_items": '[{"amount": 10.0, "qty": 2}]'}
    assert _eval(svc, pred, gt).f1 == pytest.approx(1.0)


def test_dict_json_encoded_string_vs_dict() -> None:
    svc = _service()
    pred = {"meta": {"a": 1, "b": "x"}}
    gt = {"meta": '{"a": 1.0, "b": "x"}'}
    assert _eval(svc, pred, gt).f1 == pytest.approx(1.0)


def test_fallback_exact_equality() -> None:
    svc = _service()
    pred = {"x": object()}
    gt = {"x": object()}
    assert _eval(svc, pred, gt).f1 == pytest.approx(0.0)


def test_regression_double_encoded_line_items_string_matches_list() -> None:
    svc = _service()

    # Predicted is parsed into a real list
    predicted = {
        "line_items": [
            {"date": "10/1/2023", "type": "Inv", "description": "Stapler Heavy Duty", "payment": 1000.0, "amount": 1000.0, "balance": 0.0}
        ]
    }

    # Ground truth is an escaped JSON string of the SAME list
    ground_truth = {
        "line_items": (
            "[{\"date\":\"10/1/2023\",\"type\":\"Inv\",\"description\":\"Stapler Heavy Duty\"," 
            "\"payment\":1000.0,\"amount\":1000.0,\"balance\":0.0}]"
        )
    }

    assert _eval(svc, predicted, ground_truth).f1 == pytest.approx(1.0)
