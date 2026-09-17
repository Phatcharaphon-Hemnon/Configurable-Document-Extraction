"""Active gold-set integrity guards (SROIE 8-document suite; offline).

Replaces the retired 12-file pins with equivalent checks on the active
suite. Flow analysis in docs/reports/mixed_suite_2026-09-15.md is the
authoritative leakage evidence; import tripwires only catch regressions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GOLD = REPO / "api/app/data/knowledge_base/ground_truth"
MANIFEST = GOLD / "manifest.json"
REVIEW_PKG = GOLD / "review_package_funsd_sroie.json"

ALLOWED_STATES = {"pending", "disputed", "verified", "unresolved"}
SUPPORTED_SCOPE = {"seller_address", "seller_name", "total_amount"}


def _manifest():
    import sys
    sys.path.insert(0, str(REPO / "api"))
    from app.schemas.evaluation import GoldManifest
    return GoldManifest.model_validate_json(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_inventory_and_hashes():
    m = _manifest()
    assert m.version == 2
    assert len(m.files) == 20
    assert sum(len(f.pages) for f in m.files) == 20
    assert sorted(m.release_subset) == sorted(f.filename for f in m.files)
    seen = set()
    for f in m.files:
        assert ((f.filename.startswith("sroie_") and f.filename.endswith(".jpg"))
                or (f.filename.startswith("funsd_") and f.filename.endswith(".png")))
        assert f.dataset is not None and f.dataset.source in ("sroie", "funsd")
        data = (GOLD / f.filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f.sha256
        assert [p.page_number for p in f.pages] == list(range(1, len(f.pages) + 1))
        for p in f.pages:
            key = (f.filename, p.page_number)
            assert key not in seen, f"duplicate page label {key}"
            seen.add(key)


def test_scope_totals_and_provenance_artifacts():
    m = _manifest()
    supported = unsupported = forms = 0
    for f in m.files:
        src = f.dataset.source if f.dataset else ""
        if src == "sroie":
            stem = Path(f.filename).stem
            for art in (GOLD / f"{stem}.box.txt", GOLD / f"{stem}.entities.json"):
                assert art.exists(), f"missing provenance artifact {art.name}"
                assert art.stat().st_size > 0
        for p in f.pages:
            if p.effective_kind() == "form":
                forms += 1
                assert p.doc_type is None and p.production_doc_type is None
                assert (p.annotation_scope or []) == []
                # annotation refs resolve to ACTIVE paths, never staging/tmp.
                for ref in p.annotation_refs:
                    assert "staging" not in ref.path and "tmp" not in ref.path, ref.path
                    target = GOLD / ref.path
                    assert target.exists(), ref.path
                    assert hashlib.sha256(target.read_bytes()).hexdigest() == ref.sha256
                continue
            assert set(p.annotation_scope or []) == SUPPORTED_SCOPE, f.filename
            assert list(p.unsupported_fields or []) == ["sroie_receipt_date"]
            assert p.routing_excluded is True
            supported += len(p.annotation_scope or [])
            unsupported += len(p.unsupported_fields or [])
    assert supported == 30
    assert unsupported == 10
    assert forms == 10


def test_entities_preserve_all_four_labels():
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for f in raw["files"]:
        if (f.get("dataset") or {}).get("source") != "sroie":
            continue
        stem = Path(f["filename"]).stem
        original = json.loads((GOLD / f"{stem}.entities.json").read_text(encoding="utf-8"))
        assert set(original) == {"company", "address", "date", "total"}, f["filename"]
        page = f["pages"][0]
        assert page["fields"]["seller_name"] == original["company"]
        assert page["fields"]["seller_address"] == original["address"]
        assert page["fields"]["total_amount"] == original["total"]
        assert page["fields"]["sroie_receipt_date"] == original["date"]
        assert "invoice_date" not in page["fields"]  # never silently equated


def test_values_preserve_types_and_precision():
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_file = {f["filename"]: f for f in raw["files"]}
    first = by_file["sroie_X51005301667.jpg"]["pages"][0]["fields"]
    assert first["total_amount"] == "39.80" and isinstance(first["total_amount"], str)
    assert first["sroie_receipt_date"] == "20/11/2017"
    for f in raw["files"]:
        if (f.get("dataset") or {}).get("source") != "sroie":
            continue
        assert f["pages"][0]["fields"]["seller_name"].strip() != ""


def test_review_package_pending_and_consistent():
    pkg = json.loads(REVIEW_PKG.read_text(encoding="utf-8"))
    m = _manifest()
    known = {(f.filename, p.page_number) for f in m.files for p in f.pages}
    hashes = {f.filename: f.sha256 for f in m.files}
    assert pkg["gold_manifest_sha256"] == hashlib.sha256(
        MANIFEST.read_bytes()).hexdigest()
    refs = [e["ref_id"] for e in pkg["entries"]]
    assert len(refs) == len(set(refs)) == 20
    for e in pkg["entries"]:
        assert e["human_status"] in ALLOWED_STATES
        assert e["annotator"] == "AI-assisted/provisional"
        # AI audit must never mark human-verified:
        assert e["human_status"] != "verified", e["ref_id"]
        assert e["reviewer"] is None and e["reviewed_at"] is None
        assert (e["filename"], e["page_number"]) in known
        assert e["source_sha256"] == hashes[e["filename"]]
        assert e["dataset"]["source"] in ("sroie", "funsd")


def test_review_assets_out_of_gold_discovery():
    import sys
    sys.path.insert(0, str(REPO / "api"))
    from scripts.run_eval import FORMATS
    for p in (REVIEW_PKG, GOLD / "review_viewer.html"):
        assert p.suffix.lower() not in FORMATS
    for extra in GOLD.glob("*.box.txt"):
        assert extra.suffix.lower() not in FORMATS
    for extra in GOLD.glob("*.entities.json"):
        assert extra.suffix.lower() not in FORMATS


def test_gold_scoring_only_tripwire():
    """Tripwire only — see audit report §4 for the data-flow evidence."""
    roots = ["api/app/agents/extractors.py", "api/app/agents/router.py",
             "api/app/agents/validator.py", "api/app/agents/judge.py",
             "api/app/services/rag_retriever.py",
             "api/app/services/field_catalog.py",
             "api/app/services/result_cache.py"]
    for rel in roots:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "ground_truth" not in text, rel
        assert "GoldManifest" not in text, rel
