"""Catalog/gold alignment guards (review-triage Phases 2-3).

Prevention tests:
- The extractor prompt must keep the verbatim-span + no-placeholder rules
  that stop quoted/paraphrased spans and 'no data' hallucinations.
- A field may be `required` only when the gold set proves it is printed:
  zero gold coverage for a required field forces the model to invent it
  (or flags every clean page). PO totals/currency and DN delivery_date
  were relaxed for exactly this reason (0/4 and 1/2 gold coverage).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.agents.extractors import _COMMON_RULES  # noqa: E402

KB = Path(__file__).resolve().parents[1] / "app" / "data" / "knowledge_base"
FILE_BY_DOC_TYPE = {
    "invoice": "invoice_fields.json",
    "purchase_order": "po_fields.json",
    "delivery_note": "delivery_note_fields.json",
}


def test_prompt_demands_verbatim_bare_spans():
    assert "verbatim" in _COMMON_RULES
    assert "no surrounding quotation marks or brackets" in _COMMON_RULES
    assert "no label prefixes" in _COMMON_RULES


def test_prompt_bans_no_data_placeholders():
    assert "ไม่มีข้อมูล" in _COMMON_RULES
    assert "never invent" in _COMMON_RULES


def _gold_coverage() -> dict[str, dict[str, int]]:
    manifest = json.loads((KB / "ground_truth" / "manifest.json").read_text(encoding="utf-8"))
    pages_by_type: dict[str, int] = {}
    hits: dict[str, dict[str, int]] = {}
    for entry in manifest["files"]:
        for page in entry["pages"]:
            doc_type = page["doc_type"]
            pages_by_type[doc_type] = pages_by_type.get(doc_type, 0) + 1
            for name in page["fields"]:
                hits.setdefault(doc_type, {}).setdefault(name, 0)
                hits[doc_type][name] += 1
    return {dt: {"pages": pages_by_type[dt], "hits": hits.get(dt, {})} for dt in pages_by_type}


def test_no_required_field_has_zero_gold_coverage():
    coverage = _gold_coverage()
    violations = []
    for doc_type, filename in FILE_BY_DOC_TYPE.items():
        catalog = json.loads((KB / "field_catalog" / filename).read_text(encoding="utf-8"))
        required = [f["name"] for f in catalog["fields"] if f.get("required")]
        cov = coverage.get(doc_type, {"pages": 0, "hits": {}})
        for name in required:
            if cov["hits"].get(name, 0) == 0:
                violations.append(f"{doc_type}.{name}: required but printed on 0/{cov['pages']} gold pages")
    assert violations == [], violations


def test_unprinted_totals_are_not_required():
    """Regression: PO totals/currency and DN delivery_date are optional.

    0/4 PO gold pages print totals/currency; 1/2 DN gold pages print a
    delivery date. Requiring them forces invention or permanent review.
    """
    po = json.loads((KB / "field_catalog" / "po_fields.json").read_text(encoding="utf-8"))
    by_name = {f["name"]: f for f in po["fields"]}
    assert by_name["total_amount"]["required"] is False
    assert by_name["currency"]["required"] is False

    dn = json.loads((KB / "field_catalog" / "delivery_note_fields.json").read_text(encoding="utf-8"))
    by_name = {f["name"]: f for f in dn["fields"]}
    assert by_name["delivery_date"]["required"] is False
