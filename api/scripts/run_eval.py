"""Evaluate every gold page with the real pipeline; isolated storage and catalogs.

From the repository root:
 .venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
Mock mode exercises report plumbing only and never represents live metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import REPO_ROOT, Settings  # noqa: E402
from app.schemas.documents import ExtractionResult  # noqa: E402
from app.schemas.evaluation import GoldManifest, GoldPage  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402
from app.services.field_catalog import normalize_field_name  # noqa: E402
from app.services.field_matching import values_match  # noqa: E402

# Default evaluation dataset. Activation of a replacement suite updates this
# constant (after full validation at final paths); explicit --gold-dir
# overrides always win. Do not add a competing configuration mechanism.
DEFAULT_GOLD_DIR = REPO_ROOT / "api/app/data/knowledge_base/ground_truth"

DEFAULT_SUBSET = [
    "sroie_X51005301667.jpg",
    "sroie_X51005663293.jpg",
    "sroie_X51005663297.jpg",
    "sroie_X51005663311.jpg",
    "sroie_X51005806685.jpg",
    "sroie_X51006414713.jpg",
    "sroie_X51006556815.jpg",
    "sroie_X51006857265.jpg",
    "sroie_X51008123604.jpg",
    "sroie_X51008142033.jpg",
    "funsd_0001118259.png",
    "funsd_0011973451.png",
    "funsd_0011974919.png",
    "funsd_00283813.png",
    "funsd_0060207528.png",
    "funsd_01197604.png",
    "funsd_71108371.png",
    "funsd_87533049.png",
    "funsd_91361993.png",
    "funsd_93380187.png",
]
FORMATS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
# Per-file prediction JSONs live beside gold inputs but are never gold inputs
# themselves: FORMATS excludes ".json" and --all only scans top-level files,
# so this folder cannot pollute gold selection or hash guards.
PER_FILE_DIRNAME = "eval_outputs"


def per_file_json_path(gold_dir: Path, filename: str) -> Path:
    """Result JSON path for one gold file: <gold_dir>/eval_outputs/<stem>.prediction.json."""
    return gold_dir / PER_FILE_DIRNAME / f"{Path(filename).stem}.prediction.json"


def write_per_file_json(path: Path, filename: str, response: dict, records: list[dict], seconds: float) -> None:
    """Persist one file's full prediction plus its per-page score records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "filename": filename,
                "eval_seconds": seconds,
                "error": response.get("error"),
                "documents": response.get("documents", []),
                "pages": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def expected_doc_type(stem: str) -> str:
    """Legacy helper only; live expectations come exclusively from the manifest."""
    if stem.startswith(("delivery_note", "Delivery")):
        return "delivery_note"
    return "purchase_order" if stem.startswith(("po_", "purchase")) else "invoice"


def mock_predict(ground_truth: dict) -> dict:
    prediction = dict(ground_truth)
    if prediction:
        prediction.pop(next(reversed(prediction)))
    prediction["eval_probe_field"] = "probe"
    return prediction


def match_value(predicted: Any, expected: Any) -> bool:
    # Keep digit-string IDs exact, including leading zeros. Other values follow
    # existing numeric/date comparison; names are NEVER alias-mapped.
    if isinstance(expected, str) and expected.isdigit():
        return isinstance(predicted, str) and predicted == expected
    return values_match(predicted, expected)


def apply_aliases(names: dict[str, Any], aliases: dict[str, str] | None) -> dict[str, Any]:
    """Apply an eval-local alias map symmetrically (refs and predictions).

    Keys are normalized first; aliases map normalized-alternative ->
    normalized-canonical. Two distinct names collapsing to one canonical is
    an explicit ValueError, never a silent merge.
    """
    if not aliases:
        return dict(names)
    table = {normalize_field_name(k): normalize_field_name(v) for k, v in aliases.items()}
    seen: dict[str, str] = {}
    for name in names:
        canon = table.get(name, name)
        if canon in seen and seen[canon] != name:
            raise ValueError(f"alias collision: {seen[canon]!r} and {name!r} both map to {canon!r}")
        seen[canon] = name
    return {table.get(name, name): value for name, value in names.items()}


def file_support(pages: list[GoldPage]) -> tuple[str, str]:
    """Dispatch verdict for one file's pages, before any pipeline call.

    Returns ("supported", "") when every page has a supported production
    evaluation path, else ("not_evaluated", reason). Mixed files are reported
    as an explicit limitation and never partially run: silently skipping only
    the unsupported pages of a file would misattribute shared pipeline work.
    """
    kinds = {p.effective_kind() for p in pages}
    if kinds <= {"invoice", "purchase_order", "delivery_note"}:
        return "supported", ""
    if kinds == {"form"}:
        return ("not_evaluated",
                "document_kind=form has no supported production evaluation path")
    return ("not_evaluated",
            f"mixed page kinds {sorted(kinds)} cannot be processed safely")


def not_evaluated_record(filename: str, gold: GoldPage, reason: str) -> dict:
    return dict(
        expected=gold.doc_type or gold.effective_kind(),
        predicted=None,
        router_ok=False,
        router_evaluated=False,
        language_ok=False,
        language=gold.language,
        precision=0.0,
        recall=0.0,
        f1=None,
        tp=0,
        fp=0,
        fn=0,
        fields_expected=0,
        scoreable=False,
        out_of_scope_ignored={},
        cells_total=0,
        cells_ok=0,
        columns_total=0,
        columns_found=0,
        rows_total=0,
        rows_found=0,
        extra_rows=0,
        extra_columns=0,
        excluded_cells=0,
        failed=False,
        evaluated=False,
        status="not_evaluated",
        kind=gold.effective_kind(),
        status_reason=reason,
        error=None,
        needs_review=False,
        returned=False,
        judge_status="unavailable",
        timings={},
        usage={},
        mismatches=[],
        unexpected_fields={},
        notes=gold.notes,
    )


def score_page(gold: GoldPage, doc: ExtractionResult | None,
               aliases: dict[str, str] | None = None) -> dict:
    raw_expected = {normalize_field_name(k): v for k, v in gold.fields.items()
                    if k not in gold.excluded_fields}
    raw_predicted = (
        {normalize_field_name(f.name): f.value for f in doc.fields
         if f.name not in gold.excluded_fields} if doc else {}
    )
    expected = apply_aliases(raw_expected, aliases)
    predicted_all = apply_aliases(raw_predicted, aliases)
    scope = (set(gold.annotation_scope) if gold.annotation_scope is not None
             else set(expected) | set(predicted_all))
    expected = {k: v for k, v in expected.items() if k in scope}
    out_of_scope_ignored = {k: v for k, v in predicted_all.items() if k not in scope}
    predicted = {k: v for k, v in predicted_all.items() if k in scope}
    failed = doc is None or bool(doc.error)
    if failed:
        # No invented false positives: nothing was returned, so every
        # scoreable reference is a false negative.
        tp, fp, fn = 0, 0, len(expected)
    else:
        tp = sum(k in predicted and match_value(predicted[k], value) for k, value in expected.items())
        fp = sum(k not in expected or not match_value(value, expected[k]) for k, value in predicted.items())
        fn = len(expected) - tp
    # Zero-denominator convention (documented): 0/0 -> 1.0 iff there is
    # nothing scoreable on that side, else 0.0.
    precision = tp / (tp + fp) if (tp + fp) else (1.0 if not expected else 0.0)
    recall = tp / len(expected) if expected else 1.0
    cells_total = cells_ok = columns_total = columns_found = rows_total = rows_found = excluded_cells = 0
    tables = doc.tables if doc else []
    used = set()
    for table in gold.tables:
        labels = [normalize_field_name(label) for label in table.columns]
        # Match tables by printed headers, not invented schema-key synonyms.
        candidates = [
            (len(set(labels) & {normalize_field_name(c.label) for c in t.columns}), i, t)
            for i, t in enumerate(tables)
            if i not in used
        ]
        winner = max(candidates, key=lambda item: item[0]) if candidates else None
        actual = winner[2] if winner and winner[0] else None
        if actual:
            used.add(winner[1])
        actual_labels = {normalize_field_name(c.label): c.key for c in actual.columns} if actual else {}
        columns_total += len(labels)
        columns_found += sum(label in actual_labels for label in labels)
        rows_total += len(table.rows)
        rows_found += min(len(table.rows), len(actual.rows)) if actual else 0
        for index, row in enumerate(table.rows):
            actual_row = {c.column: c.value for c in actual.rows[index]} if actual and index < len(actual.rows) else {}
            for label, expected_cell in zip(labels, row):
                if expected_cell is None:
                    excluded_cells += 1
                    continue
                cells_total += 1
                if label in actual_labels and match_value(actual_row.get(actual_labels[label]), expected_cell):
                    cells_ok += 1
    return dict(
        expected=gold.doc_type or gold.effective_kind(),
        predicted=doc.doc_type if doc else None,
        router_ok=bool(doc and doc.failed_stage not in ("ocr", "router") and doc.doc_type == gold.doc_type),
        router_evaluated=not gold.routing_excluded and gold.effective_kind() != "form",
        language_ok=bool(doc and doc.language == gold.language),
        language=gold.language,
        precision=precision,
        recall=recall,
        f1=2 * precision * recall / (precision + recall) if precision + recall else 0,
        tp=tp,
        fp=fp,
        fn=fn,
        fields_expected=len(expected),
        scoreable=bool(expected) or bool(cells_total),
        out_of_scope_ignored=out_of_scope_ignored,
        cells_total=cells_total,
        cells_ok=cells_ok,
        columns_total=columns_total,
        columns_found=columns_found,
        rows_total=rows_total,
        rows_found=rows_found,
        extra_rows=max(0, sum(len(t.rows) for t in tables) - rows_total),
        extra_columns=max(0, sum(len(t.columns) for t in tables) - columns_total),
        excluded_cells=excluded_cells,
        failed=doc is None or bool(doc.error),
        evaluated=True,
        status="evaluated" if not (doc is None or bool(doc.error)) else "failed",
        kind=gold.effective_kind(),
        error=doc.error if doc else "Missing page result",
        needs_review=bool(doc is None or doc.needs_review),
        returned=doc is not None,
        judge_status=doc.judge_status if doc else "unavailable",
        timings=doc.timings if doc else {},
        usage=doc.usage if doc else {},
        mismatches=[
            {"field": k, "expected": v, "predicted": predicted.get(k)}
            for k, v in expected.items()
            if k not in predicted or not match_value(predicted[k], v)
        ],
        unexpected_fields={k: v for k, v in predicted.items() if k not in expected},
        notes=gold.notes,
    )


def summarize(results: list[dict]) -> dict:
    n = len(results)
    evaluated = [r for r in results if r.get("evaluated", True)]
    unevaluated = [r for r in results if not r.get("evaluated", True)]
    scored = [r for r in evaluated if not r.get("failed") and r.get("f1") is not None]

    def avg(key):
        return sum(r.get(key) or 0 for r in evaluated) / len(evaluated) if evaluated else 0

    def ratio(a, b):
        denom = sum(r.get(b, 0) for r in results)
        return sum(r.get(a, 0) for r in results) / denom if denom else 0

    durations = [
        r.get("timings", {}).get("pipeline", 0)
        + r.get("timings", {}).get("ocr", 0)
        + r.get("timings", {}).get("render", 0)
        for r in results
    ]
    routed = [r for r in evaluated if r.get("router_evaluated", True)]
    scoreable = [r for r in evaluated if r.get("scoreable", True)]
    return dict(
        n_total=n,
        n_evaluated=len(evaluated),
        n_unevaluated=len(unevaluated),
        n_scored=len(scored),
        n_failed=len(evaluated) - len(scored),
        returned=sum(r.get("returned", False) for r in evaluated),
        router_accuracy=(sum(r.get("router_ok") or 0 for r in routed) / len(routed)) if routed else None,
        router_evaluated=len(routed),
        router_excluded=len(evaluated) - len(routed),
        language_accuracy=avg("language_ok"),
        macro_precision=avg("precision"),
        macro_recall=avg("recall"),
        macro_f1=avg("f1"),
        macro_f1_scoreable=(sum(r.get("f1") or 0 for r in scoreable) / len(scoreable)) if scoreable else None,
        n_scoreable=len(scoreable),
        n_empty_scope=len(evaluated) - len(scoreable),
        tp_total=sum(r.get("tp", 0) for r in evaluated),
        fp_total=sum(r.get("fp", 0) for r in evaluated),
        fn_total=sum(r.get("fn", 0) for r in evaluated),
        fields_expected_total=sum(r.get("fields_expected", 0) for r in evaluated),
        success_only_f1=statistics.mean(r["f1"] for r in scored) if scored else 0,
        needs_review_rate=avg("needs_review"),
        table_cell_accuracy=ratio("cells_ok", "cells_total"),
        column_coverage=ratio("columns_found", "columns_total"),
        row_coverage=ratio("rows_found", "rows_total"),
        median_seconds=statistics.median(durations) if durations else 0,
        p95_seconds=sorted(durations)[max(0, int(len(durations) * 0.95 + 0.999) - 1)] if durations else 0,
    )


def md(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fmt_acc(value) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def build_report(results: list[dict], config: dict, summary: dict) -> str:
    lines = [
        "# Eval report — multilingual page extraction",
        "",
        f"Generated {config.get('generated_at', '')} · **{'MOCK — NOT live metrics' if config.get('mock') else 'LIVE pipeline'}**",
        "",
        "## Run configuration",
        "",
        "| Setting | Value |",
        "|---|---|",
    ]
    lines += [f"| {md(k)} | {md(v)} |" for k, v in config.items() if k not in {"mock", "warm_ocr"}]
    lines += [
        "",
        "## End-to-end metrics (all expected pages, including failures)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Pages returned / expected | {summary['returned']} / {summary['n_total']} |",
        f"| Evaluated / not evaluated | {summary.get('n_evaluated', summary['n_total'])} / {summary.get('n_unevaluated', 0)} |",
        f"| Successful / failed pages | {summary['n_scored']} / {summary['n_failed']} |",
        f"| TP / FP / FN (fields) | {summary.get('tp_total', 0)} / {summary.get('fp_total', 0)} / {summary.get('fn_total', 0)} |",
        f"| Scoreable / empty-scope pages | {summary.get('n_scoreable', summary['n_total'])} / {summary.get('n_empty_scope', 0)} |",
        f"| Field macro precision / recall / F1 | {summary['macro_precision']:.3f} / {summary['macro_recall']:.3f} / **{summary['macro_f1']:.3f}** |",
        f"| Successful-page-only F1 | {summary['success_only_f1']:.3f} |",
        f"| Routing / language accuracy | {_fmt_acc(summary.get('router_accuracy'))} / {summary['language_accuracy']:.3f} |",
        f"| Table cell accuracy | {summary['table_cell_accuracy']:.3f} |",
        f"| Table row / column coverage | {summary['row_coverage']:.3f} / {summary['column_coverage']:.3f} |",
        f"| Review rate | {summary['needs_review_rate']:.1%} |",
        f"| Median / p95 page seconds | {summary['median_seconds']:.2f} / {summary['p95_seconds']:.2f} |",
        "",
        "## Per-page results",
        "",
        "| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        duration = sum(r.get("timings", {}).get(k, 0) for k in ("pipeline", "ocr", "render"))
        if not r.get("evaluated", True):
            outcome = "NOT EVALUATED: " + str(r.get("status_reason", ""))
        elif r.get("failed") or r.get("f1") is None:
            outcome = "FAILED: " + str(r.get("error"))
        elif r.get("needs_review"):
            outcome = "review"
        else:
            outcome = "completed"
        lines.append(
            f"| {md(r.get('stem', ''))} | {r['expected']} → {r.get('predicted') or '—'} | {r.get('precision', 0):.3f} | {r.get('recall', 0):.3f} | {(r.get('f1') or 0):.3f} | {r.get('judge_status', 'unavailable')} | {duration:.2f} | {md(outcome)} |"
        )
    for grouping in ("expected", "language", "format", "kind", "dataset_source"):
        lines += ["", f"## Breakdown by {grouping}", "", "| Group | Pages | F1 | Median seconds |", "|---|---|---|---|"]
        for group in sorted({r.get(grouping, "unknown") or "unknown" for r in results}):
            stats = summarize([r for r in results if (r.get(grouping, "unknown") or "unknown") == group])
            lines.append(f"| {group} | {stats['n_total']} | {stats['macro_f1']:.3f} | {stats['median_seconds']:.2f} |")
    lines += ["", "## Stage timing and provider usage", "",
              "| Stage | Calls recorded | Total seconds | Retries | Reported tokens |", "|---|---|---|---|---|"]
    stages = sorted({key for r in results for key in r.get("timings", {})} - {"pipeline", "ocr_cached"})
    for stage in stages:
        def usage_keys(key):
            return key == stage or (stage == "extractor" and key.startswith("extractor_"))
        usage = [value for r in results for key, value in r.get("usage", {}).items() if usage_keys(key)]
        count = sum(stage in r.get("timings", {}) for r in results)
        seconds = sum(r.get("timings", {}).get(stage, 0) for r in results)
        retries = sum(u.get("retries", 0) for u in usage)
        tokens = sum(u.get("total_tokens", 0) for u in usage)
        lines.append(f"| {stage} | {count} | {seconds:.2f} | {retries} | {tokens} |")
    lines += ["", "Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency."]
    unattributed = sum(max(0, r.get("timings", {}).get("pipeline", 0) - sum(
        value for key, value in r.get("timings", {}).items()
        if key not in {"pipeline", "ocr", "render", "ocr_cached"})) for r in results)
    lines += [f"Unattributed pipeline time: {unattributed:.2f}s (orchestration/tracing and, in older runs, failed stages without separate timing)."]
    warm = config.get("warm_ocr", {})
    if warm:
        lines += ["", "## OCR repeat measurements", "",
                  "Only OCR is repeated; no additional agent calls are made.", "",
                  "| File | First OCR + render seconds | Repeat seconds | Cache hit |", "|---|---|---|---|"]
        filenames = sorted({r.get("filename") for r in results if r.get("filename")})
        for filename in filenames:
            first = sum(r.get("timings", {}).get("ocr", 0) + r.get("timings", {}).get("render", 0)
                        for r in results if r.get("filename") == filename)
            repeat = warm.get(filename, {})
            lines.append(f"| {md(filename)} | {first:.3f} | {repeat.get('seconds', 0):.6f} | {repeat.get('cache_hit', False)} |")

    lines += [
        "",
        "## Annotation and scoring limits",
        "",
        "- Reference answers were visually transcribed by the assistant before evaluation; they have not been independently human-adjudicated.",
        "- Missing/failed expected pages remain in metric denominators. Null cells and explicitly excluded fields are unscored. Extra fields count as false positives; extra rows/columns are recorded in metrics.json.",
        "- Column matching uses exact normalized printed headers; no field or column synonym mapping. IDs preserve leading zeros. Source-language strings are retained.",
        "- Some merchant/product names are Malay, German or Afrikaans inside English-labeled documents. Language labels describe the primary document labels.",
        "- The blank Thai form and ambiguous handwriting are intentional review cases. Printed inconsistent totals are not corrected in gold answers.",
        "- Source-grounded output can still contain OCR mistakes. A review-free result is not proof of accuracy. This small developer gold set is not an independent test benchmark.",
        "- Timings describe this model/host/run; no speedup percentage is claimed without a comparable baseline.",
        "",
        "## Agents and RAG sources",
        "",
        "- Router; InvoiceExtractor, PurchaseOrderExtractor, DeliveryNoteExtractor; deterministic Validator; Judge (explicit skipped/unavailable outcomes).",
        "- Local OCR runs before text-only agents. field_catalog/*.json supplies compact catalog context.",
        "- few_shot/*/*.json and app/services/rag_retriever.py are available; few-shot defaults OFF (top-0). ground_truth/manifest.json is scoring-only and never prompt input.",
        "",
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .",
        "```",
        "",
        "Pass bar: pipeline runs on sample inputs and real metrics are reported; final-demo accuracy thresholds are not required.",
        "",
    ]
    return "\n".join(lines)


def build_combined_report(
    results: list[dict],
    subset: list[dict],
    config: dict,
    release_config: dict,
    summary: dict,
    subset_summary: dict,
) -> str:
    """One release document: full-run report followed by the fixed-subset section."""
    full = build_report(results, config, summary)
    sub_lines = build_report(subset, release_config, subset_summary).split("\n")
    if sub_lines and sub_lines[0].startswith("# "):
        sub_lines[0] = "## Release subset (fixed files from manifest.release_subset)"
    return full + "\n---\n\n" + "\n".join(sub_lines) + "\n"


async def run(args):
    gold_dir = args.gold_dir.resolve()
    manifest = GoldManifest.model_validate_json((gold_dir / "manifest.json").read_text(encoding="utf-8"))
    available = {f.filename: f for f in manifest.files}
    selected = (
        sorted(p.name for p in gold_dir.iterdir() if p.suffix.lower() in FORMATS)
        if args.all
        else args.subset or manifest.release_subset
    )
    missing = set(selected) - set(available)
    if missing:
        raise ValueError(f"Missing annotations: {sorted(missing)}")
    for name in selected:
        entry = available[name]
        if hashlib.sha256((gold_dir / name).read_bytes()).hexdigest() != entry.sha256:
            raise ValueError(f"Gold source hash changed: {name}")
        if [p.page_number for p in entry.pages] != list(range(1, len(entry.pages) + 1)):
            raise ValueError(f"Invalid page numbering: {name}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_dir = output / "eval_artifacts"
    artifact_dir.mkdir(exist_ok=True)
    settings = Settings()
    config = dict(
        mock=args.mock,
        generated_at=datetime.now(timezone.utc).isoformat(),
        provider=settings.llm_provider,
        model=settings.llm_model,
        ocr=settings.ocr_engine,
        ocr_languages=settings.ocr_languages,
        dpi=settings.ocr_dpi,
        few_shot=args.few_shot,
        concurrency=1,
        files=len(selected),
        gold_sha256=hashlib.sha256((gold_dir / "manifest.json").read_bytes()).hexdigest(),
        revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        working_tree="uncommitted implementation",
        implementation_sha256=hashlib.sha256(b"".join(
            str(p.relative_to(REPO_ROOT)).encode() + p.read_bytes()
            for p in sorted((REPO_ROOT / "api/app").rglob("*.py"))
        )).hexdigest(),
        source_hashes={name: available[name].sha256 for name in selected},
    )
    results = []
    responses = {}
    Path(settings.cache_path).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eval-", dir=settings.cache_path) as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(Path(settings.knowledge_base_path) / "field_catalog", kb / "field_catalog")
        if args.few_shot:
            shutil.copytree(Path(settings.knowledge_base_path) / "few_shot", kb / "few_shot")
        settings.ocr_cache_path = str(Path(tmp) / "ocr-results")
        settings.knowledge_base_path = str(kb)
        settings.database_enabled = False
        settings.audit_log_enabled = False
        settings.temporal_enabled = False
        settings.source_storage_path = str(Path(tmp) / "sources")
        settings.few_shot_examples_per_doc_type = args.few_shot
        service = DocumentExtractionService(settings)
        config["ocr_model_hashes"] = service.ocr.model_hashes
        for name in selected:
            print(f"Processing {name} ({len(available[name].pages)} expected pages)", flush=True)
            started = time.perf_counter()
            verdict, reason = file_support(available[name].pages)
            if verdict == "not_evaluated":
                # Guard before inference: no mock fabrication, no
                # extract_group (OCR/Router/Extractor/Validator/Judge), no
                # catalog contact for unsupported pages.
                print(f"  not evaluated: {reason}", flush=True)
                for gold in available[name].pages:
                    record = not_evaluated_record(name, gold, reason)
                    record.update(
                        stem=f"{name} / {gold.page_number}",
                        filename=name,
                        page_number=gold.page_number,
                        format=Path(name).suffix.lower(),
                        dataset_source=(available[name].dataset.source
                                        if available[name].dataset else ""),
                    )
                    results.append(record)
                responses[name] = {"documents": [], "error": None,
                                   "eval_seconds": time.perf_counter() - started,
                                   "not_evaluated": reason}
                continue
            if args.mock:
                docs = []
                for expected in available[name].pages:
                    docs.append(
                        ExtractionResult(
                            doc_type=expected.doc_type,
                            language=expected.language,
                            needs_review=True,
                            fields=[
                                dict(name=k, value=v, confidence=0.5, source_span=str(v))
                                for k, v in mock_predict(expected.fields).items()
                            ],
                        )
                    )
                response = {"documents": [d.model_dump(mode="json") for d in docs]}
            else:
                try:
                    res = await service.extract_group(
                        [UploadedFilePart(name, mimetypes.guess_type(name)[0], (gold_dir / name).read_bytes())]
                    )
                    docs = res.documents
                    response = res.model_dump(mode="json")
                except Exception as exc:
                    docs = []
                    response = {"documents": [], "error": str(exc)}
            file_seconds = time.perf_counter() - started
            response["eval_seconds"] = file_seconds
            responses[name] = response
            # Record results incrementally: an interrupted run cannot silently look complete.
            file_start = len(results)
            for i, gold in enumerate(available[name].pages):
                record = score_page(gold, docs[i] if i < len(docs) else None)
                record.update(
                    stem=f"{name} / {gold.page_number}",
                    filename=name,
                    page_number=gold.page_number,
                    format=Path(name).suffix.lower(),
                    dataset_source=(available[name].dataset.source
                                    if available[name].dataset else ""),
                )
                results.append(record)
                print(
                    f"  page {gold.page_number}: F1={record['f1']:.3f}, judge={record['judge_status']}, failed={record['failed']}",
                    flush=True,
                )
            response["extra_pages"] = max(0, len(docs) - len(available[name].pages))
            write_per_file_json(
                per_file_json_path(gold_dir, name), name, response, results[file_start:], file_seconds
            )
            (artifact_dir / "predictions.json").write_text(
                json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (artifact_dir / "metrics.partial.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        # Warm OCR measurements only, no repeat LLM calls; record actual cache hits.
        warm = {}
        if not args.mock:
            for name in selected:
                start = time.perf_counter()
                await service.ocr.aparse_file((gold_dir / name).read_bytes(), name)
                warm[name] = {
                    "seconds": time.perf_counter() - start,
                    "cache_hit": all(p.cached for p in service.ocr.last_pages),
                }
        config["warm_ocr"] = warm
        for agent in [service.router, *service.extractors.values(), service.judge]:
            await agent._client._client.close()
    summary = summarize(results)
    subset = [r for r in results if r["filename"] in manifest.release_subset]
    metrics = {
        "config": config,
        "summary": summary,
        "subset_summary": summarize(subset),
        "pages": results,
        "extra_pages": sum(r.get("extra_pages", 0) for r in responses.values()),
    }
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix = "mock_" if args.mock else ""
    # Single release document: full run plus the fixed-subset section (no separate eval.md).
    release_config = {**config, "report_scope": "fixed release subset section inside this report"}
    report_name = f"{prefix}eval_report.md"
    (output / report_name).write_text(
        build_combined_report(results, subset, config, release_config, summary, summarize(subset)),
        encoding="utf-8",
    )
    print(
        f"Report: {output / report_name}; pages={summary['returned']}/{summary['n_total']}; F1={summary['macro_f1']:.3f}",
        flush=True,
    )
    return 0 if summary["n_scored"] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--subset", nargs="+")
    parser.add_argument("--few-shot", type=int, default=0)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        print(f"Evaluation setup failed: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
