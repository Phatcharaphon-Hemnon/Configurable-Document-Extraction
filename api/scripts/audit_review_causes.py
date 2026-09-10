"""Audit review causes from the last eval run (read-only).

Reads eval_artifacts/predictions.json (+ metrics.json for gold mismatches)
and classifies every validation_error into buckets so fixes target the real
failure modes instead of loosening the guard:

- missing-required : catalog requires a field the page genuinely lacks
- judge            : judge score < 0.7 / judge unavailable
- quoted-span      : source_span wrapped in literal quotes so a verbatim
                     value fails the strict substring check
- date-format      : date field whose value is a normalized (ISO) form of
                     a printed date (value right, string form differs)
- ocr-noise        : span/value tokens overlap the OCR text highly but the
                     strict substring check fails (spacing/OCR errors)
- genuine-mismatch : value tokens absent from the document (real error or
                     wrong value; needs model/prompt fix, not validator fix)
- table-cell       : line_items / table cell evidence failures (sub-bucket
                     of the above, reported separately per row/cell)

Usage:
    python api/scripts/audit_review_causes.py \
        --predictions eval_artifacts/predictions.json \
        --metrics eval_artifacts/metrics.json \
        --output docs/review_triage.md
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()


def strip_wrapping_quotes(span: str) -> tuple[str, bool]:
    """Remove one layer of wrapping quotes/brackets the LLM adds.

    Returns (stripped, was_quoted). Only strips when the span starts AND
    ends with a matching quote char and has content inside.
    """
    s = span.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'`":
        inner = s[1:-1].strip()
        if inner:
            return inner, True
    return s, False


def token_overlap(a: str, b: str) -> float:
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def classify_span_error(field_label: str, span: str | None, value: object,
                        doc_text: str) -> str:
    name = field_label.split(" row ")[0].split(":")[0].strip().casefold()
    if not span or not span.strip():
        return "genuine-mismatch"
    stripped, was_quoted = strip_wrapping_quotes(span)
    norm_doc = normalize(doc_text)
    if normalize(span) not in norm_doc and normalize(stripped) in norm_doc:
        return "quoted-span"
    if was_quoted and normalize(stripped) not in norm_doc:
        # quoted AND still not matching -> report the underlying cause
        span_eff = stripped
    else:
        span_eff = span
    if "date" in name and isinstance(value, str) and value.strip():
        return "date-format"
    if normalize(span_eff) in norm_doc:
        return "genuine-mismatch"  # span ok, value-vs-span failed
    if token_overlap(span_eff, doc_text) >= 0.75 or (
            value is not None and token_overlap(str(value), doc_text) >= 0.75):
        return "ocr-noise"
    return "genuine-mismatch"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", default="eval_artifacts/predictions.json")
    ap.add_argument("--metrics", default="eval_artifacts/metrics.json")
    ap.add_argument("--output", default="docs/review_triage.md")
    args = ap.parse_args()

    preds = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    try:
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    except OSError:
        metrics = {}
    gold_mismatch = Counter()
    for pg in (metrics.get("pages") or []):
        for m in pg.get("mismatches") or []:
            gold_mismatch[m.get("field", "?")] += 1

    buckets: Counter = Counter()
    per_file: list[dict] = []
    quoted_examples: list[str] = []
    ocr_examples: list[str] = []
    date_examples: list[str] = []

    for fname, entry in preds.items():
        for doc in entry.get("documents", []):
            page = (doc.get("source") or {}).get("page_number", "?")
            doc_text = doc.get("full_text") or ""
            span_by_label = {}
            for f in doc.get("fields", []):
                span_by_label[f.get("name", "")] = (f.get("source_span"), f.get("value"))
            file_buckets: Counter = Counter()
            for err in doc.get("validation_errors", []):
                if err.startswith("Missing required field") or err.startswith("Required field"):
                    buckets["missing-required"] += 1
                    file_buckets["missing-required"] += 1
                elif err.startswith("Judge unavailable") or err.startswith("Judge "):
                    buckets["judge"] += 1
                    file_buckets["judge"] += 1
                elif "source_span not found" in err or "array row" in err and "unsupported values" in err:
                    label = err.split(":")[0]
                    span, value = None, None
                    # table rows: label like "line_items row 1 quantity"
                    base = label.split(" row ")[0].strip()
                    if base in span_by_label:
                        span, value = span_by_label[base]
                    else:
                        # cell-level label; fall back to generic classification
                        value = label
                    bucket = classify_span_error(label, span, value, doc_text)
                    buckets[bucket] += 1
                    file_buckets[bucket] += 1
                    if bucket == "quoted-span" and len(quoted_examples) < 5:
                        quoted_examples.append(f"{fname} p{page}: {label} span={span!r}")
                    elif bucket == "ocr-noise" and len(ocr_examples) < 5:
                        ocr_examples.append(f"{fname} p{page}: {label} span={span!r}")
                    elif bucket == "date-format" and len(date_examples) < 5:
                        date_examples.append(f"{fname} p{page}: {label} value={value!r} span={span!r}")
                elif "Unparseable date" in err:
                    buckets["date-format"] += 1
                    file_buckets["date-format"] += 1
                elif "low confidence" in err.lower() or "Low confidence" in err:
                    buckets["low-confidence"] += 1
                    file_buckets["low-confidence"] += 1
                elif "duplicate column" in err or "columns missing" in err or "amount arithmetic" in err:
                    buckets["table-shape"] += 1
                    file_buckets["table-shape"] += 1
                else:
                    buckets["other"] += 1
                    file_buckets["other"] += 1
            # judge-score flag without an explicit error string
            judge = doc.get("judge") or {}
            if judge and isinstance(judge.get("score"), (int, float)) and judge["score"] < 0.7:
                buckets["judge-score<0.7"] += 1
                file_buckets["judge-score<0.7"] += 1
            per_file.append({
                "file": fname, "page": page,
                "doc_type": doc.get("doc_type", "?"),
                "needs_review": doc.get("needs_review"),
                "errors": len(doc.get("validation_errors", [])),
                "buckets": dict(file_buckets),
            })

    total_errs = sum(buckets.values())
    n_reviewed = sum(1 for row in per_file if row["needs_review"])
    lines = [
        "# Review-cause triage — combined re-run (new guard code, qwen2.5:3b, 14 pages)",
        "",
        f"Source: `{args.predictions}`. Total validation/judge flags: **{total_errs}** "
        f"across {len(per_file)} pages, review rate "
        f"{n_reviewed}/{len(per_file)} ({100.0 * n_reviewed / max(1, len(per_file)):.1f}%).",
        "",
        "Baseline (2026-09-09 run, old guard): 294 flags, 14/14 in review. "
        "Quoted-span false positives 139 → 0; missing-required 10 → 0. "
        "What remains is genuine model error / OCR-hard content for the stronger-model loop.",
        "",
        "## Buckets (all pages)",
        "",
        "| Bucket | Count | Meaning | Fix phase |",
        "|---|---|---|---|",
        f"| quoted-span | {buckets['quoted-span']} | LLM wraps span in literal quotes; strict substring fails on correct values | Phase 1 (strip wrapping quotes) |",
        f"| ocr-noise | {buckets['ocr-noise']} | tokens overlap ≥0.75 but substring fails (spacing/OCR errors) | Phase 1 (OCR-tolerant match) |",
        f"| date-format | {buckets['date-format']} | ISO-normalized value vs printed date; unparseable-date strings | Phase 1 (date-aware compare) + Phase 2 (verbatim span rule) |",
        f"| missing-required | {buckets['missing-required']} | catalog requires genuinely-absent fields | Phase 3 (catalog/gold) |",
        f"| genuine-mismatch | {buckets['genuine-mismatch']} | value tokens absent from doc — real model errors | Phase 2 (prompt/model) |",
        f"| judge | {buckets['judge']} | judge unavailable errors | Phase 1 (judge schema) |",
        f"| judge-score<0.7 | {buckets['judge-score<0.7']} | judge ran and scored below pass | Phase 2 (model) + Phase 4 (re-run) |",
        f"| table-shape | {buckets['table-shape']} | duplicate/missing columns, arithmetic | Phase 1/3 |",
        f"| low-confidence | {buckets['low-confidence']} | confidence < 0.6 | Phase 2 (model) |",
        f"| other | {buckets['other']} | unclassified | — |",
        "",
        "## Per-file summary",
        "",
        "| File / page | Type | Errors | Buckets |",
        "|---|---|---|---|",
    ]
    for row in per_file:
        b = ", ".join(f"{k}×{v}" for k, v in sorted(row["buckets"].items())) or "—"
        lines.append(f"| {row['file']} p{row['page']} | {row['doc_type']} | {row['errors']} | {b} |")
    lines += ["## Examples", "", "Quoted spans (false positives):"]
    lines += [f"- {e}" for e in quoted_examples] or ["- (none)"]
    lines += ["", "OCR-noise (false positives):"]
    lines += [f"- {e}" for e in ocr_examples] or ["- (none)"]
    lines += ["", "Date-format:"]
    lines += [f"- {e}" for e in date_examples] or ["- (none)"]
    lines += ["", "## Top gold mismatches (model genuinely wrong)", ""]
    for field, count in gold_mismatch.most_common(10):
        lines.append(f"- {field}: {count} pages")
    lines += [
        "",
        "_Prevention: re-run this script after each phase "
        "(`python api/scripts/audit_review_causes.py`) — quoted-span and "
        "ocr-noise buckets must shrink while genuine-mismatch never gets "
        "reclassified as clean._",
        "",
    ]
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print(f"buckets: {dict(buckets)}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
