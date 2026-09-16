"""Merge two eval runs (3h cap forces chunking) into one combined report.

- run A: eval_rerun/eval_artifacts/{predictions.json,metrics.partial.json}
         (killed at cap; ICR.png failed with an extractor timeout)
- run B: eval_rerun2/eval_artifacts/{predictions.json,metrics.partial.json}
         (remaining files; ICR.png timed out again -> stays failed)

Uses run_eval's own summarize/build_combined_report so numbers match a
single-run report. Config comes from run B (same code revision for both).

Usage:
    .venv/bin/python api/scripts/merge_eval_runs.py \
        --a eval_rerun --b eval_rerun2 --output eval_final
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "api"))

from scripts.run_eval import build_combined_report, summarize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="eval_rerun")
    ap.add_argument("--b", default="eval_rerun2")
    ap.add_argument("--output", default="eval_final")
    args = ap.parse_args()

    a_dir = (REPO_ROOT / args.a / "eval_artifacts").resolve()
    b_dir = (REPO_ROOT / args.b / "eval_artifacts").resolve()
    out = (REPO_ROOT / args.output).resolve()
    (out / "eval_artifacts").mkdir(parents=True, exist_ok=True)

    preds_a = json.loads((a_dir / "predictions.json").read_text(encoding="utf-8"))
    preds_b = json.loads((b_dir / "predictions.json").read_text(encoding="utf-8"))
    results_a = json.loads((a_dir / "metrics.partial.json").read_text(encoding="utf-8"))
    results_b = json.loads((b_dir / "metrics.partial.json").read_text(encoding="utf-8"))

    # ICR.png failed in run A; run B retried it (failed again). Prefer run B
    # records wherever a file exists in both (same code, fresher attempt).
    by_file_b = {r["filename"]: r for r in results_b}
    results = [r for r in results_a if r["filename"] not in by_file_b] + results_b
    responses = {**preds_a, **preds_b}

    metrics_b = json.loads((b_dir / "metrics.json").read_text(encoding="utf-8"))
    config = dict(metrics_b["config"])
    config["files"] = sorted(responses.keys())
    config["combined_from"] = [args.a, args.b]
    config["report_scope"] = "combined full run across two chunks (3h cap)"

    summary = summarize(results)
    manifest = json.loads(
        (REPO_ROOT / "api/app/data/knowledge_base/ground_truth/manifest.json").read_text(encoding="utf-8")
    )
    subset = [r for r in results if r["filename"] in manifest["release_subset"]]
    metrics = {
        "config": config,
        "summary": summary,
        "subset_summary": summarize(subset),
        "pages": results,
        "extra_pages": sum(r.get("extra_pages", 0) for r in responses.values()),
    }
    (out / "eval_artifacts" / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "eval_artifacts" / "metrics.partial.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "eval_artifacts" / "predictions.json").write_text(
        json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
    release_config = {**config, "report_scope": "fixed release subset section inside this report"}
    (out / "eval_report.md").write_text(
        build_combined_report(results, subset, config, release_config, summary, summarize(subset)),
        encoding="utf-8",
    )
    clean = sum(1 for f in responses.values() for d in f["documents"] if not d.get("needs_review"))
    total = sum(len(f["documents"]) for f in responses.values())
    print(f"pages={summary['returned']}/{summary['n_total']} scored={summary['n_scored']} "
          f"failed={summary['n_failed']} macroF1={summary['macro_f1']:.3f} "
          f"review_rate={summary['needs_review_rate']:.3f} clean={clean}/{total}")


if __name__ == "__main__":
    main()
