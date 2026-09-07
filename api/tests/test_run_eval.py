"""Tests for the eval script helpers (mock mode only — no LLM/OCR calls)."""

from __future__ import annotations

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from scripts.run_eval import (  # noqa: E402
    DEFAULT_SUBSET,
    build_report,
    expected_doc_type,
    mock_predict,
    summarize,
)


def test_default_subset_covers_all_doc_types():
    assert len(DEFAULT_SUBSET) == 8
    assert any(s.startswith("invoice") for s in DEFAULT_SUBSET)
    assert any(s.startswith("po_") for s in DEFAULT_SUBSET)
    assert any(s.startswith("delivery_note") for s in DEFAULT_SUBSET)
    assert len(set(DEFAULT_SUBSET)) == len(DEFAULT_SUBSET)


def test_expected_doc_type_prefixes():
    assert expected_doc_type("invoice_01") == "invoice"
    assert expected_doc_type("po_03") == "purchase_order"
    assert expected_doc_type("delivery_note_02") == "delivery_note"


def test_mock_predict_drops_one_adds_one():
    gt = {"a": 1, "b": 2, "c": 3}
    pred = mock_predict(gt)
    assert "eval_probe_field" in pred
    assert len(pred) == len(gt)  # one dropped, one added
    assert sum(1 for k in gt if k not in pred) == 1


def test_summarize_and_report_shape():
    results = [
        {"stem": "invoice_01", "expected": "invoice", "predicted": "invoice",
         "router_ok": True, "precision": 1.0, "recall": 0.5, "f1": 0.667,
         "needs_review": True, "judge_score": 0.8, "judge_skipped": False, "notes": ""},
        {"stem": "po_99", "expected": "purchase_order", "f1": None, "error": "boom"},
    ]
    summary = summarize(results)
    assert summary["n_total"] == 2
    assert summary["n_scored"] == 1
    assert summary["n_failed"] == 1
    assert summary["router_accuracy"] == 1.0

    report = build_report(results, {"mock": True, "few_shot": 2}, summary)
    assert "# Eval report" in report
    assert "MOCK" in report
    assert "invoice_01" in report
    assert "FAILED: boom" in report
    assert "rag_retriever" in report
