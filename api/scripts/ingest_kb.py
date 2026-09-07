"""Ingest the project knowledge base into a retrieval index (offline).

Walks the file-backed KB (field catalog, few-shot examples, ground truth,
sample documents) and writes a TF-IDF retrieval index used by
``app.services.rag_retriever`` to rank few-shot examples per document.

Usage (from api/):
    source ../.venv/bin/activate
    python scripts/ingest_kb.py [--kb app/data/knowledge_base] [--json]

Output:
    <kb>/rag_index.json   — {"doc_types": {...}, "meta": {...}}
    Prints per-doc-type counts plus the RAG source inventory.

Exit code 0 always (missing folders yield empty sections, never crash).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rag_retriever import build_index  # noqa: E402

_FEW_SHOT_FOLDERS = ("invoice", "po", "delivery_note")
_CATALOG_FILES = ("invoice_fields.json", "po_fields.json", "delivery_note_fields.json")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def ingest(kb_dir: Path) -> dict:
    """Build the index dict and source inventory for *kb_dir*."""
    grouped: dict[str, list[dict]] = {}
    few_shot_counts: dict[str, int] = {}
    for folder in _FEW_SHOT_FOLDERS:
        examples = []
        for path in sorted((kb_dir / "few_shot" / folder).glob("*.json")):
            data = _read_json(path)
            if data is not None:
                data.setdefault("_source_file", path.name)
                examples.append(data)
        grouped[folder] = examples
        few_shot_counts[folder] = len(examples)

    catalog_counts: dict[str, int] = {}
    for filename in _CATALOG_FILES:
        data = _read_json(kb_dir / "field_catalog" / filename)
        fields = data.get("fields", []) if data else []
        catalog_counts[filename] = len(fields) if isinstance(fields, list) else 0

    ground_truth = sorted((kb_dir / "ground_truth").glob("*.json")) if (kb_dir / "ground_truth").exists() else []
    documents = sorted((kb_dir / "documents").glob("*.pdf")) if (kb_dir / "documents").exists() else []

    index = build_index(grouped)
    index["meta"] = {
        "kb_dir": str(kb_dir),
        "few_shot_counts": few_shot_counts,
        "catalog_field_counts": catalog_counts,
        "ground_truth_files": len(ground_truth),
        "document_pdfs": len(documents),
        "retriever": "tfidf-cosine (stdlib, offline)",
    }
    report = {
        "index": index,
        "inventory": {
            "few_shot": few_shot_counts,
            "catalog_fields": catalog_counts,
            "ground_truth_files": [p.name for p in ground_truth],
            "document_pdfs": [p.name for p in documents],
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the KB into rag_index.json")
    parser.add_argument("--kb", default="app/data/knowledge_base")
    parser.add_argument("--json", action="store_true", help="print machine-readable inventory")
    args = parser.parse_args()

    kb_dir = Path(args.kb)
    if not kb_dir.is_absolute():
        # Resolve relative to api/ (this script lives in api/scripts/).
        kb_dir = Path(__file__).resolve().parents[1] / args.kb
    report = ingest(kb_dir)

    out_path = kb_dir / "rag_index.json"
    try:
        out_path.write_text(json.dumps(report["index"], ensure_ascii=False, indent=2), encoding="utf-8")
        wrote = str(out_path)
    except OSError as exc:
        print(f"WARNING: could not write {out_path}: {exc}")
        wrote = ""

    if args.json:
        print(json.dumps(report["inventory"], indent=2, ensure_ascii=False))
        return

    inv = report["inventory"]
    total_ex = sum(inv["few_shot"].values())
    total_fields = sum(inv["catalog_fields"].values())
    print("KB ingestion complete")
    print(f"  index: {wrote or '(write failed)'}")
    print(f"  few-shot examples: {total_ex} {inv['few_shot']}")
    print(f"  catalog fields: {total_fields} {inv['catalog_fields']}")
    print(f"  ground-truth files: {len(inv['ground_truth_files'])}")
    print(f"  sample PDFs: {len(inv['document_pdfs'])}")
    print("RAG sources: field_catalog/*.json + few_shot/*/*.json (ranked per query)")


if __name__ == "__main__":
    main()
