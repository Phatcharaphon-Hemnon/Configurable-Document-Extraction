"""Tests for the offline TF-IDF RAG retriever + KB ingest script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPOROOT = Path(__file__).resolve().parents[2]
if str(_REPOROOT) not in sys.path:
    sys.path.insert(0, str(_REPOROOT))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ingest_kb import ingest  # noqa: E402

from app.services.rag_retriever import (  # noqa: E402
    build_index,
    rank_examples,
    score_against_index,
    tokenize,
)


def _examples():
    return [
        {"description": "PO", "input_text": "purchase order PO-99 supplier Acme order quantity delivery"},
        {"description": "INV", "input_text": "tax invoice INV-1 seller total amount balance due statement"},
        {"description": "DN", "input_text": "delivery note shipment tracking courier parcel received"},
    ]


def test_tokenize_basic():
    assert "invoice" in tokenize("Tax INVOICE No: INV-001")
    assert tokenize("") == []
    assert all(len(t) > 1 for t in tokenize("a bb ccc"))


def test_rank_prefers_lexically_similar():
    ranked = rank_examples(_examples(), "tax invoice total amount due", 3)
    assert ranked[0]["description"] == "INV"
    assert len(ranked) == 3


def test_rank_limit_and_empty_query_stable_order():
    examples = _examples()
    assert rank_examples(examples, "", 2) == examples[:2]
    assert rank_examples(examples, "   ", 2) == examples[:2]
    assert rank_examples(examples, "invoice", 0) == []
    assert len(rank_examples(examples, "invoice", 10)) == 3


def test_build_index_and_score_roundtrip():
    index = build_index({"invoice": _examples()})
    entry = index["doc_types"]["invoice"]
    assert entry["n_docs"] == 3
    assert "invoice" in entry["doc_freq"]
    scores = [
        score_against_index(doc, "tax invoice total", entry["doc_freq"], entry["n_docs"])
        for doc in entry["docs"]
    ]
    assert scores[1] == max(scores)


def test_ingest_reads_real_kb_layout(tmp_path):
    kb = tmp_path / "kb"
    (kb / "few_shot" / "invoice").mkdir(parents=True)
    (kb / "few_shot" / "po").mkdir(parents=True)
    (kb / "few_shot" / "delivery_note").mkdir(parents=True)
    (kb / "few_shot" / "invoice" / "example_01.json").write_text(
        json.dumps({"description": "INV", "input_text": "invoice total"}), encoding="utf-8"
    )
    (kb / "field_catalog").mkdir(parents=True)
    (kb / "field_catalog" / "invoice_fields.json").write_text(
        json.dumps({"fields": [{"name": "a"}]}), encoding="utf-8"
    )
    (kb / "ground_truth").mkdir(parents=True)
    (kb / "ground_truth" / "invoice_01.json").write_text("{}", encoding="utf-8")
    (kb / "documents").mkdir(parents=True)
    (kb / "documents" / "invoice_01.pdf").write_bytes(b"%PDF-1.4 fake")

    report = ingest(kb)
    assert report["index"]["doc_types"]["invoice"]["n_docs"] == 1
    assert report["inventory"]["catalog_fields"]["invoice_fields.json"] == 1
    assert report["inventory"]["ground_truth_files"] == ["invoice_01.json"]
    assert report["inventory"]["document_pdfs"] == ["invoice_01.pdf"]
