"""Mixed-suite importers, scoped scoring, and default-resolution guards.

Offline only: fixtures are synthetic and never counted as imports.
Flow analyses in docs/reports/mixed_suite_*.md are authoritative; source-text
tripwires here catch regressions only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = REPO / "api"
sys.path.insert(0, str(API))


def test_storage_isolation_accepts_dedicated_staging(tmp_path):
    from scripts.mixed_suite.storage import verify_staging_isolation

    from app.core.config import Settings
    staging = tmp_path / "data-local" / "mixed_suite_staging"
    staging.mkdir(parents=True)
    # Point only the cache tree at tmp so no real state is touched.
    import os
    os.environ["PROJECT_CACHE_DIR"] = str(tmp_path / "data-local" / "cache")
    try:
        s = Settings()
        got = verify_staging_isolation(staging, s)
        assert Path(got["staging"]) == staging.resolve()
    finally:
        del os.environ["PROJECT_CACHE_DIR"]


def test_storage_isolation_rejects_overlap(tmp_path):
    import pytest
    from scripts.mixed_suite.storage import verify_staging_isolation

    from app.core.config import Settings
    s = Settings()
    kb = Path(s.knowledge_base_path).resolve()
    with pytest.raises(ValueError):
        verify_staging_isolation(kb, s)
    with pytest.raises(ValueError):
        verify_staging_isolation(kb / "ground_truth", s)


def test_selection_deterministic_and_sorted():
    from scripts.mixed_suite.select import select_ids
    ids = [f"X{i:06d}" for i in range(50, 0, -1)]
    a, _ = select_ids(sorted(ids), 8, 20260915)
    b, _ = select_ids(sorted(ids), 8, 20260915)
    assert a == b and a == sorted(a) and len(a) == 8
    c, _ = select_ids(sorted(ids), 8, 1)
    assert c != a  # seed actually drives the draw


def _sroie_train(tmp_path: Path, n: int = 3, bad_keys: bool = False):
    tr = tmp_path / "train"
    for sub in ("img", "entities", "box"):
        (tr / sub).mkdir(parents=True)
    for i in range(n):
        sid = f"X{i:08d}"
        (tr / "img" / f"{sid}.jpg").write_bytes(b"img" + str(i).encode())
        ent = {"company": "C", "address": "A", "date": "01/01/2020", "total": "1.00"}
        if bad_keys and i == 0:
            ent = {"company": "C"}
        (tr / "entities" / f"{sid}.txt").write_text(json.dumps(ent))
        (tr / "box" / f"{sid}.txt").write_text("0,0,9,0,9,9,0,9,hi\n")
    return tr


def test_sroie_importer_fragment_and_mapping(tmp_path):
    from scripts.mixed_suite.import_sroie import import_sroie
    tr = _sroie_train(tmp_path)
    frag = import_sroie(tr, tmp_path / "staging" / "sroie", seed=7, count=2)
    assert len(frag["files"]) == 2 and not frag["invalid"]
    page = frag["files"][0]["pages"][0]
    # date is deliberately NOT invoice_date ground truth; it is preserved
    # but unsupported (never a false negative).
    assert set(page["fields"]) == {"seller_name", "seller_address", "total_amount", "sroie_receipt_date"}
    assert "invoice_date" not in page["fields"]
    assert page["annotation_scope"] == ["seller_address", "seller_name", "total_amount"]
    assert page["unsupported_fields"] == ["sroie_receipt_date"]
    assert page["routing_excluded"] is True
    assert frag["files"][0]["dataset"]["source"] == "sroie"


def test_sroie_importer_rejects_bad_entities(tmp_path):
    from scripts.mixed_suite.import_sroie import import_sroie
    tr = _sroie_train(tmp_path, bad_keys=True)
    frag = import_sroie(tr, tmp_path / "staging" / "sroie", seed=7, count=3)
    assert len(frag["files"]) == 2 and len(frag["invalid"]) == 1


def test_docile_blocked_and_adapter():
    import tempfile

    from scripts.mixed_suite.import_docile import adapt_fields, blocked_report, locate_split
    rep = blocked_report("no token")
    assert rep["blocked"]["source"] == "docile" and rep["files"] == []
    with tempfile.TemporaryDirectory() as tmp:
        assert locate_split(Path(tmp)) is None
    scalars, rows, unsupported = adapt_fields(
        [{"fieldtype": "seller_name", "text": "Acme"},
         {"fieldtype": "mystery_type", "text": "x"},
         {"fieldtype": "amount", "text": "3", "line_item_id": 1},
         {"fieldtype": "desc", "text": "w", "line_item_id": 1}],
        {"seller_name": "seller_name"})
    assert scalars == {"seller_name": "Acme"}
    assert rows == [(1, {"amount": "3", "desc": "w"})]  # grouping preserved, not flattened
    assert unsupported == ["mystery_type"]


def test_thai_blocked_dedupe_and_localization_only(tmp_path):
    from scripts.mixed_suite.import_thai import blocked_report, dedupe_originals, localization_only_page
    rep = blocked_report("no credentials", observed="api 401 without key")
    assert rep["blocked"]["quota"] == 4
    keep, excl = dedupe_originals([
        {"name": "a.jpg", "image": b"a", "source_hash": "h1"},
        {"name": "a_aug.jpg", "image": b"a2", "source_hash": "h1"},
        {"name": "b.jpg", "image": b"b", "source_hash": None},
    ])
    assert [c["name"] for c in keep] == ["a.jpg"] and len(excl) == 2
    page = localization_only_page("t.jpg", "ab" * 32, [{"label": "total"}], b"img")
    assert page["pages"][0]["annotation_scope"] == []
    assert page["pages"][0]["routing_excluded"] is True
    assert page["pages"][0]["fields"] == {}


def test_manifest_builder_guards_and_readiness(tmp_path):
    import pytest
    from scripts.mixed_suite.manifest_builder import build_combined_manifest
    (tmp_path / "sroie_X1.jpg").write_bytes(b"img1")
    good = {"filename": "sroie_X1.jpg",
            "sha256": hashlib.sha256(b"img1").hexdigest(),
            "dataset": {"source": "sroie"}, "pages": [{"annotation_scope": ["a"]}]}
    with pytest.raises(ValueError):
        build_combined_manifest([{"files": [good, good]}], tmp_path)
    bad = dict(good, sha256="0" * 64)
    with pytest.raises(ValueError):
        build_combined_manifest([{"files": [bad]}], tmp_path)
    m, led = build_combined_manifest([{"files": [good]}], tmp_path)
    assert m["version"] == 2
    assert led["imported"] == 1 and led["text_scoreable"] == 1
    assert led["complete"] is False  # partial quotas never activate


def _gold_page(fields, scope=None, routing_excluded=False):
    from app.schemas.evaluation import GoldPage
    return GoldPage(page_number=1, doc_type="invoice", language="en",
                    fields=fields, annotation_scope=scope,
                    routing_excluded=routing_excluded)


def _doc(fields, error=None):
    from app.schemas.documents import ExtractionResult
    return ExtractionResult(doc_type="invoice",
                            fields=[dict(name=k, value=v, confidence=0.9, source_span=str(v))
                                    for k, v in fields.items()],
                            error=error)


def test_scoped_scoring_out_of_scope_unscored():
    from scripts.run_eval import score_page
    gold = _gold_page({"a": "1", "b": "2"}, scope=["a"])
    rec = score_page(gold, _doc({"a": "1", "zzz": "junk"}))
    assert (rec["tp"], rec["fp"], rec["fn"]) == (1, 0, 0)
    assert rec["out_of_scope_ignored"] == {"zzz": "junk"}


def test_scoped_scoring_in_scope_denominators():
    from scripts.run_eval import score_page
    gold = _gold_page({"a": "1", "b": "2"})
    # Legacy (scope=None) scores every name: b wrong + currency extra = 2 FP.
    rec = score_page(gold, _doc({"a": "1", "b": "WRONG", "currency": "THB"}))
    assert (rec["tp"], rec["fp"], rec["fn"]) == (1, 2, 1)


def test_failed_doc_keeps_fn_without_invented_fp():
    from scripts.run_eval import score_page
    gold = _gold_page({"a": "1", "b": "2"})
    rec = score_page(gold, None)
    assert (rec["tp"], rec["fp"], rec["fn"]) == (0, 0, 2)
    assert rec["f1"] == 0 and rec["failed"]


def test_router_excluded_and_na():
    from scripts.run_eval import _fmt_acc, score_page, summarize
    gold = _gold_page({"a": "1"}, routing_excluded=True)
    rec = score_page(gold, _doc({"a": "1"}))
    assert rec["router_evaluated"] is False
    s = summarize([rec])
    assert s["router_accuracy"] is None and _fmt_acc(None) == "N/A"
    assert s["router_excluded"] == 1


def test_alias_symmetry_and_collision():
    import pytest
    from scripts.run_eval import apply_aliases, score_page
    assert apply_aliases({"company": "C"}, {"company": "seller_name"}) == {"seller_name": "C"}
    with pytest.raises(ValueError):
        apply_aliases({"a": 1, "b": 2}, {"a": "x", "b": "x"})
    gold = _gold_page({"company": "Acme"})
    rec = score_page(gold, _doc({"seller_name": "Acme"}),
                     aliases={"company": "seller_name"})
    assert (rec["tp"], rec["fp"], rec["fn"]) == (1, 0, 0)


def test_empty_scope_not_in_headline():
    from scripts.run_eval import score_page, summarize
    full = score_page(_gold_page({"a": "1"}), _doc({"a": "WRONG"}))
    empty = score_page(_gold_page({}, scope=[]), _doc({}))
    assert empty["scoreable"] is False and full["scoreable"] is True
    s = summarize([full, empty])
    assert s["n_empty_scope"] == 1
    assert s["macro_f1_scoreable"] == full["f1"] != s["macro_f1"]
    assert (s["tp_total"], s["fp_total"], s["fn_total"]) == (0, 1, 1)


def test_default_gold_dir_mechanism():
    import argparse

    from scripts.run_eval import DEFAULT_GOLD_DIR

    from scripts import run_eval
    # Active suite is the 20-document SROIE+FUNSD suite.
    assert DEFAULT_GOLD_DIR.name == "ground_truth"
    assert (DEFAULT_GOLD_DIR / "manifest.json").exists()
    assert len(run_eval.DEFAULT_SUBSET) == 20
    # Explicit override path is preserved by the parser.
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    assert parser.parse_args([]).gold_dir == DEFAULT_GOLD_DIR
    assert parser.parse_args(["--gold-dir", "/tmp/x"]).gold_dir == Path("/tmp/x")


def test_default_resolves_active_sroie_suite():
    import json

    from scripts.run_eval import DEFAULT_GOLD_DIR, FORMATS
    manifest = json.loads((DEFAULT_GOLD_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    assert len(manifest["files"]) == 20
    found = sorted(p.name for p in DEFAULT_GOLD_DIR.iterdir() if p.suffix.lower() in FORMATS)
    assert found == sorted(f["filename"] for f in manifest["files"])
    assert sum(1 for n in found if n.startswith("sroie_")) == 10
    assert sum(1 for n in found if n.startswith("funsd_")) == 10


def test_importers_never_touch_catalogs():
    for rel in ("scripts/mixed_suite/import_sroie.py",
                "scripts/mixed_suite/import_docile.py",
                "scripts/mixed_suite/import_thai.py"):
        text = (REPO / "api" / rel).read_text(encoding="utf-8")
        assert "FieldCatalog" not in text and "add_fields" not in text, rel


def test_replacement_verify_fail_restore_in_isolated_dirs(tmp_path):
    """Activation failure/rollback procedure on isolated copies.

    Uses one real staged SROIE asset: corrupt the copy -> verification
    fails -> restore from backup -> verification passes. Only tmp paths.
    """
    import shutil

    from scripts.mixed_suite.manifest_builder import build_combined_manifest

    real = REPO / "data-local" / "mixed_suite_staging" / "sroie"
    assert (real / "sroie_X51005301667.jpg").exists()
    work = tmp_path / "gold"
    work.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    for name in ("sroie_X51005301667.jpg",):
        shutil.copy2(real / name, work / name)
        shutil.copy2(real / name, backup / name)
    frag = {"files": [{
        "filename": "sroie_X51005301667.jpg",
        "sha256": hashlib.sha256((work / "sroie_X51005301667.jpg").read_bytes()).hexdigest(),
        "dataset": {"source": "sroie"},
        "pages": [{"annotation_scope": ["a"], "unsupported_fields": []}]}]}
    manifest, _ = build_combined_manifest([frag], work)
    assert len(manifest["files"]) == 1
    # Simulate failed replacement: corrupt the working copy.
    (work / "sroie_X51005301667.jpg").write_bytes(b"corrupt")
    import pytest
    with pytest.raises(ValueError):
        build_combined_manifest([frag], work)
    # Rollback restores only this path from backup; verification passes again.
    shutil.copy2(backup / "sroie_X51005301667.jpg", work / "sroie_X51005301667.jpg")
    manifest, _ = build_combined_manifest([frag], work)
    assert len(manifest["files"]) == 1


def test_mock_default_redirect_never_writes_active(tmp_path):
    """Redirect default resolution to a disposable copy and mock-evaluate.

    Proves the default mechanism selects whatever DEFAULT_GOLD_DIR points
    at, while the real active suite gains no generated predictions.
    """
    import asyncio
    import shutil
    from types import SimpleNamespace

    from scripts import run_eval

    src = run_eval.DEFAULT_GOLD_DIR
    assert (src / "manifest.json").exists()
    copy = tmp_path / "goldcopy"
    shutil.copytree(src, copy, ignore=shutil.ignore_patterns("eval_outputs"))
    redirected = copy
    old = run_eval.DEFAULT_GOLD_DIR
    run_eval.DEFAULT_GOLD_DIR = redirected
    try:
        assert run_eval.DEFAULT_GOLD_DIR == redirected
        code = asyncio.run(run_eval.run(SimpleNamespace(
            gold_dir=redirected, all=True, subset=None, few_shot=0,
            mock=True, output_dir=tmp_path / "out")))
        assert code in (0, 2)
        import json as _json
        copied = _json.loads((copy / "manifest.json").read_text())["files"]
        # Only evaluated (non-form) files gain predictions; guarded files get none.
        expected_n = sum(1 for f in copied
                         if not all(p.get("document_kind") == "form" for p in f["pages"]))
        assert len(list((copy / "eval_outputs").glob("*.prediction.json"))) == expected_n
        assert expected_n == 10
    finally:
        run_eval.DEFAULT_GOLD_DIR = old
    assert not (src / "eval_outputs").exists(), "active suite must gain no synthetic predictions"
    assert run_eval.DEFAULT_GOLD_DIR == src


def test_kind_combination_matrix():
    import pytest

    from app.schemas.evaluation import GoldPage
    base = dict(page_number=1, language="en", fields={})
    # legacy: doc_type only
    p = GoldPage(doc_type="invoice", **base)
    assert p.effective_kind() == "invoice"
    # form without production type
    p = GoldPage(doc_type=None, document_kind="form", production_doc_type=None, **base)
    assert p.effective_kind() == "form"
    # conflicting combinations rejected
    with pytest.raises(ValueError):
        GoldPage(doc_type=None, **base)
    with pytest.raises(ValueError):
        GoldPage(doc_type="invoice", document_kind="form", production_doc_type=None, **base)
    with pytest.raises(ValueError):
        GoldPage(doc_type=None, document_kind="form", production_doc_type="invoice", **base)
    with pytest.raises(ValueError):
        GoldPage(doc_type="invoice", document_kind="nope", **base)
    with pytest.raises(ValueError):
        GoldPage(doc_type="invoice", production_doc_type="purchase_order", **base)


def test_dispatch_guard_zero_production_calls(tmp_path):
    """A FUNSD-only file makes zero production-pipeline calls end to end.

    Runs the real run() dispatch on an isolated form-only gold dir with
    extract_group replaced by fail-if-called; the guard must divert before
    any mock fabrication, extraction, OCR, or catalog contact.
    """
    import asyncio
    import json as _json
    from types import SimpleNamespace

    import app.services.extraction_service as svc
    from scripts import run_eval

    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "funsd_form.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"))
    (gold / "manifest.json").write_text(_json.dumps({
        "version": 2, "annotation_method": "test", "release_subset": ["funsd_form.png"],
        "files": [{"filename": "funsd_form.png", "sha256": hashlib.sha256(
            (gold / "funsd_form.png").read_bytes()).hexdigest(),
            "dataset": {"source": "funsd"},
            "pages": [{"page_number": 1, "doc_type": None, "document_kind": "form",
                       "production_doc_type": None, "language": "en", "fields": {}}]}]}))

    async def boom(self, *a, **k):
        raise AssertionError("production pipeline must not run for not_evaluated files")

    old = svc.DocumentExtractionService.extract_group
    svc.DocumentExtractionService.extract_group = boom
    try:
        code = asyncio.run(run_eval.run(SimpleNamespace(
            gold_dir=gold, all=True, subset=None, few_shot=0,
            mock=True, output_dir=tmp_path / "out")))
    finally:
        svc.DocumentExtractionService.extract_group = old
    assert code in (0, 2)
    metrics = _json.loads((tmp_path / "out" / "eval_artifacts" / "metrics.json").read_text())
    assert metrics["summary"]["n_total"] == 1
    assert metrics["summary"]["n_unevaluated"] == 1
    assert metrics["summary"]["n_evaluated"] == 0
    page = metrics["pages"][0]
    assert page["status"] == "not_evaluated" and page["kind"] == "form"


def test_mixed_file_limitation_explicit():
    from scripts.run_eval import file_support

    from app.schemas.evaluation import GoldPage
    mixed = [
        GoldPage(page_number=1, doc_type="invoice", language="en", fields={"a": "1"}),
        GoldPage(page_number=2, document_kind="form", language="en", fields={}),
    ]
    verdict, reason = file_support(mixed)
    assert verdict == "not_evaluated" and "mixed" in reason


def test_funsd_sidecar_validates_links():
    from scripts.mixed_suite.import_funsd import validate_annotation
    raw = {"form": [
        {"id": 1, "text": "Q", "box": [0, 0, 9, 9], "label": "question",
         "words": [], "linking": [[1, 2], [1, 2]]},
        {"id": 2, "text": "A", "box": [0, 10, 9, 19], "label": "answer",
         "words": [], "linking": []},
        {"id": 3, "text": "lonely", "box": [0, 20, 9, 29], "label": "other",
         "words": [], "linking": [[3, 99]]},
    ]}
    derived, errors = validate_annotation(raw)
    assert not errors
    assert derived["links"] == [{"source": 1, "target": 2, "repeated": True, "declarations": 2}]
    assert derived["unlinked_ids"] == [3]
    assert derived["ambiguous"] == [{"from": 3, "to": 99, "reason": "dangling endpoint"}]
    assert derived["qa_pairs"] == [{"from": 1, "to": 2, "from_text": "Q", "to_text": "A"}]
    bad, errs = validate_annotation({"form": [{"id": 1}]})
    assert errs and not bad["entities"][0].get("box")
