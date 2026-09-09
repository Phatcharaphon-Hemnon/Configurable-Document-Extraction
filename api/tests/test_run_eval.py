"""Tests for the eval script helpers (mock mode only — no LLM/OCR calls)."""

from __future__ import annotations

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from scripts.run_eval import (  # noqa: E402
    DEFAULT_SUBSET,
    FORMATS,
    PER_FILE_DIRNAME,
    build_combined_report,
    build_report,
    expected_doc_type,
    mock_predict,
    per_file_json_path,
    summarize,
    write_per_file_json,
)


def test_default_subset_covers_all_doc_types():
    assert len(DEFAULT_SUBSET) == 8
    assert "Invoice1.jpg" in DEFAULT_SUBSET
    assert "purchase_orders1.pdf" in DEFAULT_SUBSET
    assert "Delivery1.webp" in DEFAULT_SUBSET
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
    assert summary["router_accuracy"] == 0.5

    report = build_report(results, {"mock": True, "few_shot": 2}, summary)
    assert "# Eval report" in report
    assert "MOCK" in report
    assert "invoice_01" in report
    assert "FAILED: boom" in report
    assert "rag_retriever" in report


def test_missing_page_stays_in_accuracy_denominator():
    from scripts.run_eval import score_page

    from app.schemas.evaluation import GoldPage
    gold = GoldPage(page_number=1, doc_type='invoice', language='th', fields={'invoice_number':'001'})
    metric = score_page(gold, None)
    assert metric['f1'] == 0 and metric['failed'] and not metric['returned']
    assert summarize([metric])['n_total'] == 1


def test_ids_keep_leading_zero_and_no_alias_matching():
    from scripts.run_eval import match_value, score_page

    from app.schemas.documents import ExtractionResult
    from app.schemas.evaluation import GoldPage
    assert not match_value(123.0, '00123')
    gold = GoldPage(page_number=1, doc_type='invoice', language='en', fields={'invoice_number':'00123'})
    doc = ExtractionResult(doc_type='invoice', fields=[dict(name='invoice_id', value='00123',confidence=.9,source_span='00123')])
    assert score_page(gold, doc)['f1'] == 0


def test_per_file_json_path_stays_out_of_gold_selection(tmp_path):
    path = per_file_json_path(tmp_path, "Invoice+purchase.pdf")
    assert path.parent == tmp_path / PER_FILE_DIRNAME
    assert path.suffix == ".json"
    assert path.suffix not in FORMATS  # never picked up by --all gold scan
    assert path.name == "Invoice+purchase.prediction.json"


def test_write_per_file_json_roundtrip(tmp_path):
    target = per_file_json_path(tmp_path, "THAI_bill.jpg")
    response = {"documents": [{"doc_type": "invoice"}], "error": None}
    records = [{"stem": "THAI_bill.jpg / 1", "f1": 0.5}]
    write_per_file_json(target, "THAI_bill.jpg", response, records, 12.5)
    import json

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["filename"] == "THAI_bill.jpg"
    assert saved["eval_seconds"] == 12.5
    assert saved["documents"] == response["documents"]
    assert saved["pages"] == records


def test_combined_report_holds_full_and_subset_sections():
    rows = [
        {"stem": "a / 1", "filename": "a", "expected": "invoice", "predicted": "invoice",
         "router_ok": True, "precision": 1.0, "recall": 1.0, "f1": 1.0,
         "needs_review": False, "judge_score": 0.9, "judge_skipped": False, "notes": ""},
        {"stem": "b / 1", "filename": "b", "expected": "invoice", "predicted": "invoice",
         "router_ok": True, "precision": 0.5, "recall": 0.5, "f1": 0.5,
         "needs_review": True, "judge_score": 0.5, "judge_skipped": False, "notes": ""},
    ]
    config = {"mock": True, "few_shot": 0}
    combined = build_combined_report(rows, rows[:1], config, config, summarize(rows), summarize(rows[:1]))
    assert combined.count("# Eval report") == 1  # single title
    assert "## Release subset" in combined
    assert combined.index("## Release subset") > combined.index("## Per-page results")
    assert "b / 1" in combined  # full section keeps all pages
