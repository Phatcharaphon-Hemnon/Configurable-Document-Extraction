"""Eval the extraction pipeline against a gold subset (v0.2.0 AI core).

Runs the full pipeline (local RapidOCR → Router → Extractor → Validator →
Judge, with RAG-ranked few-shot) on N sample PDFs from the KB and scores
each prediction against its ground-truth JSON (precision/recall/F1 via
``DocumentExtractionService.evaluate``). Writes a markdown metrics report.

Usage (from api/):
    source ../.venv/bin/activate
    python scripts/run_eval.py [--mock] [--few-shot 2] [--output ../../eval_report.md]
    python scripts/run_eval.py --subset invoice_01 po_01 delivery_note_01 --output eval_tmp.md

Modes:
    real (default) — live pipeline; needs LLM_API_KEY in api/.env.
        Slow (one LLM call chain per doc); failures are recorded honestly
        per item (failed_stage / error) and still count in the report.
    --mock — deterministic offline scoring (prediction = ground truth minus
        the last key plus one bogus field). For CI smoke tests only; the
        report is clearly labeled MOCK and must not be mistaken for live
        metrics.

Exit code 0 always; interpret the printed summary + report file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Default gold subset: 8 items covering all 3 doc types.
DEFAULT_SUBSET = [
    "invoice_01",
    "invoice_04",
    "invoice_05",
    "po_01",
    "po_02",
    "po_03",
    "delivery_note_01",
    "delivery_note_02",
]

_EXPECTED_BY_PREFIX = (
    ("delivery_note", "delivery_note"),
    ("invoice", "invoice"),
    ("po", "purchase_order"),
)


def expected_doc_type(stem: str) -> str:
    for prefix, doc_type in _EXPECTED_BY_PREFIX:
        if stem.startswith(prefix):
            return doc_type
    return "invoice"


def load_gold(kb_dir: Path, stem: str) -> tuple[bytes, dict[str, Any]]:
    pdf_path = kb_dir / "documents" / f"{stem}.pdf"
    gt_path = kb_dir / "ground_truth" / f"{stem}.json"
    return pdf_path.read_bytes(), json.loads(gt_path.read_text(encoding="utf-8"))


def mock_predict(ground_truth: dict[str, Any]) -> dict[str, Any]:
    """Deterministic stand-in prediction: drop the last key, add one bogus key."""
    prediction = dict(ground_truth)
    if prediction:
        prediction.pop(next(reversed(prediction)))
    prediction["eval_probe_field"] = "probe"
    return prediction


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if r.get("f1") is not None]
    router_ok = sum(1 for r in scored if r.get("router_ok"))
    return {
        "n_total": len(results),
        "n_scored": len(scored),
        "n_failed": len(results) - len(scored),
        "router_accuracy": (router_ok / len(scored)) if scored else 0.0,
        "macro_precision": sum(r["precision"] for r in scored) / len(scored) if scored else 0.0,
        "macro_recall": sum(r["recall"] for r in scored) / len(scored) if scored else 0.0,
        "macro_f1": sum(r["f1"] for r in scored) / len(scored) if scored else 0.0,
        "needs_review_rate": (sum(1 for r in scored if r.get("needs_review")) / len(scored)) if scored else 0.0,
    }


def build_report(
    results: list[dict[str, Any]],
    config: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    mode_tag = "MOCK (offline, deterministic — NOT live pipeline metrics)" if config.get("mock") else "LIVE pipeline"
    lines = [
        "# Eval report — v0.2.0 AI core",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · mode: **{mode_tag}**_",
        "",
        "## Config",
        "",
        "| Setting | Value |",
        "|---|---|",
    ]
    for key, value in config.items():
        if key == "mock":
            continue
        lines.append(f"| `{key}` | `{value}` |")
    lines += [
        "",
        "## Summary",
        "",
        f"- Items: {summary['n_scored']}/{summary['n_total']} scored ({summary['n_failed']} failed)",
        f"- Router accuracy: {summary['router_accuracy']:.3f}",
        f"- Macro precision / recall / F1: {summary['macro_precision']:.3f} / "
        f"{summary['macro_recall']:.3f} / **{summary['macro_f1']:.3f}**",
        f"- needs_review rate: {summary['needs_review_rate']:.1%}",
        "",
        "## Per-document results",
        "",
        "| doc | expected | predicted | router | P | R | F1 | review? | judge | notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("f1") is None:
            lines.append(
                f"| {r['stem']} | {r['expected']} | — | — | — | — | — | — | — | "
                f"FAILED: {r.get('error', 'unknown')[:80]} |"
            )
        else:
            judge = "skipped" if r.get("judge_skipped") else (f"{r['judge_score']:.2f}" if r.get("judge_score") is not None else "n/a")
            lines.append(
                f"| {r['stem']} | {r['expected']} | {r['predicted']} | "
                f"{'ok' if r['router_ok'] else 'WRONG'} | {r['precision']:.3f} | "
                f"{r['recall']:.3f} | {r['f1']:.3f} | "
                f"{'yes' if r['needs_review'] else 'no'} | {judge} | {r.get('notes', '')} |"
            )
    lines += [
        "",
        "## Agents exercised",
        "",
        "- Router (LLM, structured `RoutingResponseSchema` → Pydantic `RoutingDecision`)",
        "- Extractor ×3 — invoice / purchase_order / delivery_note "
        "(LLM, structured `ExtractionResponseSchema` → `ExtractedField`)",
        "- Validator (deterministic: required fields, evidence/source_span, confidence, dates)",
        "- Judge (LLM, structured `JudgeResponseSchema` → `JudgeResult`; skipped when clean)",
        "",
        "## RAG sources",
        "",
        "- `field_catalog/*.json` — compact catalog in every extractor prompt",
        "- `few_shot/*/*.json` — TF-IDF ranked per document "
        f"(top-{config.get('few_shot', '?')} after ~2k-char cap; retriever: `app/services/rag_retriever.py`)",
        "- `ground_truth/*.json` — scoring only, never in prompts",
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd api && source ../.venv/bin/activate",
        "python scripts/run_eval.py --output ../eval_report.md",
        "```",
        "",
        "> Pass bar for v0.2.0: pipeline runs on sample inputs and metrics are "
        "reported (thresholds not required yet).",
        "",
    ]
    return "\n".join(lines)


async def _eval_one_real(service, stem: str, kb_dir: Path, sem: asyncio.Semaphore) -> dict[str, Any]:
    from app.services.extraction_service import UploadedFilePart

    expected = expected_doc_type(stem)
    row: dict[str, Any] = {"stem": stem, "expected": expected}
    try:
        raw, ground_truth = load_gold(kb_dir, stem)
    except OSError as exc:
        return {**row, "f1": None, "error": f"missing gold data: {exc}"}
    async with sem:
        try:
            response = await service.extract_group([UploadedFilePart(f"{stem}.pdf", "application/pdf", raw)])
        except Exception as exc:  # noqa: BLE001 — recorded per item
            return {**row, "f1": None, "error": f"pipeline exception: {exc}"[:200]}
    if response.error or not response.documents:
        return {**row, "f1": None, "error": (response.error or "no documents")[:200]}
    doc = response.documents[0]
    if doc.error or doc.failed_stage:
        return {**row, "f1": None, "error": (doc.error or f"failed_stage={doc.failed_stage}")[:200]}
    prediction = {f.name: f.value for f in doc.fields}
    scored = service.evaluate(prediction=prediction, ground_truth=ground_truth, doc_type=doc.doc_type)
    notes = []
    if doc.validation_errors:
        notes.append(f"{len(doc.validation_errors)} validation error(s)")
    if any(f.is_new_field for f in doc.fields):
        notes.append(f"{sum(1 for f in doc.fields if f.is_new_field)} new field(s)")
    return {
        **row,
        "predicted": doc.doc_type,
        "router_ok": doc.doc_type == expected,
        "precision": scored.precision,
        "recall": scored.recall,
        "f1": scored.f1,
        "mismatches": len(scored.mismatches),
        "needs_review": doc.needs_review,
        "judge_score": doc.judge.score if doc.judge else None,
        "judge_skipped": doc.judge is None,
        "notes": "; ".join(notes),
    }


def _eval_one_mock(stem: str, kb_dir: Path) -> dict[str, Any]:
    expected = expected_doc_type(stem)
    row: dict[str, Any] = {"stem": stem, "expected": expected}
    try:
        _, ground_truth = load_gold(kb_dir, stem)
    except OSError as exc:
        return {**row, "f1": None, "error": f"missing gold data: {exc}"}
    # Pretend the router is always right and the judge is skipped in mock mode.
    from app.services.extraction_service import DocumentExtractionService  # noqa: E402

    service = DocumentExtractionService.__new__(DocumentExtractionService)
    scored = DocumentExtractionService.evaluate(
        service, prediction=mock_predict(ground_truth), ground_truth=ground_truth
    )
    return {
        **row,
        "predicted": expected,
        "router_ok": True,
        "precision": scored.precision,
        "recall": scored.recall,
        "f1": scored.f1,
        "mismatches": len(scored.mismatches),
        "needs_review": False,
        "judge_score": None,
        "judge_skipped": True,
        "notes": "mock prediction",
    }


async def run_real(subset: list[str], kb_dir: Path, few_shot: int, concurrency: int):
    from app.core.config import Settings  # noqa: E402
    from app.services.extraction_service import DocumentExtractionService  # noqa: E402

    settings = Settings()
    settings.database_enabled = False  # eval must not pollute extraction.db
    settings.audit_log_enabled = False
    settings.few_shot_examples_per_doc_type = few_shot
    # Pin the KB to the one beside this script — Settings defaults resolve
    # relative to cwd, which may be the repo root (legacy duplicate KB) when
    # invoked as `python api/scripts/run_eval.py`. Runnable from either dir.
    settings.knowledge_base_path = str(kb_dir)
    service = DocumentExtractionService(settings=settings)
    # Snapshot the field catalog: live extractions auto-register AI-discovered
    # names, and eval must not mutate the committed catalog files.
    catalog_dir = kb_dir / "field_catalog"
    snapshot = {
        path.name: path.read_bytes()
        for path in sorted(catalog_dir.glob("*.json"))
        if path.is_file()
    }
    sem = asyncio.Semaphore(max(1, concurrency))
    try:
        return await asyncio.gather(*[_eval_one_real(service, stem, kb_dir, sem) for stem in subset]), settings
    finally:
        for name, content in snapshot.items():
            try:
                (catalog_dir / name).write_bytes(content)
            except OSError as exc:
                # Best-effort restore must never mask eval results, but a
                # failure here means the catalog may be dirty — say so loudly.
                print(f"WARNING: catalog restore failed for {name}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval pipeline on a gold subset")
    parser.add_argument("--subset", nargs="*", default=DEFAULT_SUBSET)
    parser.add_argument("--few-shot", type=int, default=2)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[2] / "eval_report.md"))
    args = parser.parse_args()

    api_dir = Path(__file__).resolve().parents[1]
    kb_dir = api_dir / "app" / "data" / "knowledge_base"

    if args.mock:
        results = [_eval_one_mock(stem, kb_dir) for stem in args.subset]
        config = {
            "mock": True,
            "subset": ",".join(args.subset),
            "few_shot": args.few_shot,
            "note": "mock mode: no LLM/OCR calls; deterministic probe prediction",
        }
    else:
        results, settings = asyncio.run(run_real(args.subset, kb_dir, args.few_shot, args.concurrency))
        config = {
            "mock": False,
            "subset": ",".join(args.subset),
            "few_shot": args.few_shot,
            "router_model": settings.router_model_name,
            "extraction_model": settings.extraction_model_name,
            "judge_model": settings.judge_model_name,
            "ocr": f"RapidOCR local (dpi={settings.ocr_dpi})",
            "judge_skip_when_clean": settings.judge_skip_when_clean,
        }

    summary = summarize(results)
    report = build_report(results, config, summary)
    out_path = Path(args.output)
    out_path.write_text(report, encoding="utf-8")

    print(f"Eval: {summary['n_scored']}/{summary['n_total']} scored, "
          f"macro F1={summary['macro_f1']:.3f}, router acc={summary['router_accuracy']:.3f}")
    for r in results:
        status = f"F1={r['f1']:.3f}" if r.get("f1") is not None else f"FAILED: {r.get('error')}"
        print(f"  {r['stem']}: {status}")
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
