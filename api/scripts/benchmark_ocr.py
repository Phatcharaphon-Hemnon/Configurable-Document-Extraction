"""Benchmark OCR engines against identical gold references (unchanged LLM).

Compares ``tesseract`` (+fallback), ``rapidocr`` (PP-OCRv5 TH), and ``hybrid``
(TH→EN + TrOCR) on the same gold pages with the same LLM settings. Reports
field accuracy, table-cell accuracy, handwriting transcription errors,
review rate, CPU page latency, and peak RSS.

Usage (from repo root)::

    .venv/bin/python api/scripts/benchmark_ocr.py --engines tesseract,rapidocr,hybrid \\
        --gold-dir api/app/data/knowledge_base/ground_truth --output-dir eval_artifacts

Handwriting references: if ``<gold-dir>/handwriting/`` contains manually
verified ``*.json`` + image pairs (``<stem>.json`` with ``{"text": ...}`` or
``{"transcription": ...}``), TrOCR-eligible transcription error (mean
normalised edit distance) is reported per engine. Add references when the
gold set lacks handwriting — do not claim handwriting gains without them.

LLM settings are NOT changed by this script (same provider/model/timeouts);
only ``Settings.ocr_engine`` varies per run. Each engine gets isolated
storage (temp dirs) so history/sources never pollute.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import tempfile
import time
from copy import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.schemas.evaluation import GoldManifest  # noqa: E402
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart  # noqa: E402

try:
    from run_eval import DEFAULT_SUBSET, score_page  # noqa: E402
except ImportError:  # script invoked as api.scripts.benchmark_ocr
    from api.scripts.run_eval import DEFAULT_SUBSET, score_page  # type: ignore[no-redef]


def _rss_mb() -> float:
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _edit_distance(a: str, b: str) -> int:
    # Small Levenshtein for handwriting CER (references are short lines).
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[lb]


async def _run_engine(engine: str, gold_dir: Path, files: list[str], base: Settings) -> dict:
    settings = copy(base)
    settings.ocr_engine = engine
    settings.database_enabled = False
    tmp = Path(tempfile.mkdtemp(prefix=f"bench-{engine}-"))
    settings.database_path = str(tmp / "bench.db")
    settings.source_storage_path = str(tmp / "sources")
    settings.cache_path = str(tmp / "cache")
    settings.ocr_cache_path = str(tmp / "cache" / "ocr-results")
    service = DocumentExtractionService(settings=settings)

    manifest = GoldManifest.model_validate(
        json.loads((gold_dir / "manifest.json").read_text(encoding="utf-8"))
    )
    pages_by_file: dict[str, list] = {}
    for entry in manifest.files:
        if entry.filename in files:
            pages_by_file[entry.filename] = entry.pages

    records: list[dict] = []
    ocr_lat: list[float] = []
    pipe_lat: list[float] = []
    rss_before = _rss_mb()
    errors: list[str] = []
    for filename in files:
        path = gold_dir / filename
        if not path.is_file():
            errors.append(f"{filename}: gold file missing")
            continue
        raw = path.read_bytes()
        started = time.perf_counter()
        try:
            response = await service.extract_group(
                [UploadedFilePart(filename, None, raw)]
            )
        except Exception as exc:  # noqa: BLE001 — engine failures are results.
            errors.append(f"{filename}: pipeline failed: {exc}")
            continue
        wall = time.perf_counter() - started
        docs = response.documents
        gold_pages = pages_by_file.get(filename, [])
        for idx, gold in enumerate(gold_pages):
            doc = docs[idx] if idx < len(docs) else None
            rec = score_page(gold, doc)
            rec["filename"] = filename
            rec["engine"] = engine
            rec["wall_seconds"] = wall / max(1, len(gold_pages))
            records.append(rec)
            if doc is not None:
                ocr_lat.append(float(doc.timings.get("ocr", 0.0)))
                pipe_lat.append(float(doc.timings.get("pipeline", 0.0)))
    rss_peak = _rss_mb()
    n = len(records)
    field_acc = sum(r.get("tp", 0) for r in records) / max(1, sum(r.get("fields_expected", 0) for r in records))
    cell_acc = sum(r.get("cells_ok", 0) for r in records) / max(1, sum(r.get("cells_total", 0) for r in records))
    review_rate = sum(1 for r in records if r.get("needs_review")) / max(1, n)
    f1 = sum(r.get("f1", 0.0) for r in records) / max(1, n)
    return {
        "engine": engine,
        "n_pages": n,
        "field_accuracy": field_acc,
        "table_cell_accuracy": cell_acc,
        "mean_f1": f1,
        "review_rate": review_rate,
        "mean_ocr_seconds": sum(ocr_lat) / max(1, len(ocr_lat)),
        "mean_pipeline_seconds": sum(pipe_lat) / max(1, len(pipe_lat)),
        "peak_rss_mb": rss_peak,
        "rss_delta_mb": max(0.0, rss_peak - rss_before),
        "errors": errors,
        "records": records,
    }


async def _handwriting_error(engine: str, gold_dir: Path, base: Settings) -> dict:
    """Mean normalised edit distance on manual handwriting references."""
    hw_dir = gold_dir / "handwriting"
    if not hw_dir.is_dir():
        return {"engine": engine, "n_refs": 0, "note": "no handwriting/ refs — add manually verified lines"}
    refs = sorted(hw_dir.glob("*.json"))
    if not refs:
        return {"engine": engine, "n_refs": 0, "note": "handwriting/ empty — add manually verified lines"}
    settings = copy(base)
    settings.ocr_engine = engine
    settings.ocr_cache_enabled = False
    service = DocumentExtractionService(settings=settings)
    # Isolate OCR only (no LLM): compare OCR text to reference transcription.
    dists: list[float] = []
    for ref_path in refs:
        try:
            ref = json.loads(ref_path.read_text(encoding="utf-8"))
            expected = str(ref.get("transcription") or ref.get("text") or "").strip()
            image_name = ref.get("image") or ref_path.stem
            candidates = [hw_dir / image_name, hw_dir / f"{ref_path.stem}.png",
                          hw_dir / f"{ref_path.stem}.jpg"]
            img_path = next((p for p in candidates if p.is_file()), None)
            if not expected or img_path is None:
                continue
            texts = await service.ocr.aparse_file(img_path.read_bytes(), img_path.name)
            predicted = " ".join(texts).strip()
            denom = max(1, len(expected))
            dists.append(_edit_distance(predicted, expected) / denom)
        except Exception:
            dists.append(1.0)
    return {
        "engine": engine,
        "n_refs": len(dists),
        "mean_normalised_edit_distance": sum(dists) / max(1, len(dists)),
    }


async def _main_async(args) -> dict:
    base = Settings()
    engines = [e.strip().lower() for e in args.engines.split(",") if e.strip()]
    gold_dir = Path(args.gold_dir)
    files = list(DEFAULT_SUBSET)
    if args.files:
        files = [f.strip() for f in args.files.split(",") if f.strip()]
    out: dict = {"engines": {}, "handwriting": {}, "llm": {
        "provider": base.llm_provider, "model": base.llm_model,
        "note": "unchanged across engines",
    }}
    for engine in engines:
        print(f"[benchmark] engine={engine} files={len(files)}", flush=True)
        try:
            out["engines"][engine] = await _run_engine(engine, gold_dir, files, base)
        except Exception as exc:  # noqa: BLE001 — one engine must not kill others.
            out["engines"][engine] = {"engine": engine, "error": str(exc)}
        try:
            out["handwriting"][engine] = await _handwriting_error(engine, gold_dir, base)
        except Exception as exc:  # noqa: BLE001
            out["handwriting"][engine] = {"engine": engine, "error": str(exc)}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark OCR engines (tesseract,rapidocr,hybrid).")
    parser.add_argument("--engines", default="tesseract,rapidocr,hybrid")
    parser.add_argument("--gold-dir", default="api/app/data/knowledge_base/ground_truth")
    parser.add_argument("--files", default="")
    parser.add_argument("--output-dir", default="eval_artifacts")
    args = parser.parse_args()
    out = asyncio.run(_main_async(args))
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark_ocr.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# OCR benchmark (unchanged LLM: "
             f"{out['llm']['provider']}/{out['llm']['model']})", ""]
    for engine, res in out["engines"].items():
        if "error" in res and "n_pages" not in res:
            lines.append(f"## {engine}: ERROR {res['error']}")
            continue
        lines.append(
            f"## {engine}: field_acc={res['field_accuracy']:.3f} "
            f"cell_acc={res['table_cell_accuracy']:.3f} f1={res['mean_f1']:.3f} "
            f"review={res['review_rate']:.2f} ocr={res['mean_ocr_seconds']:.1f}s "
            f"pipe={res['mean_pipeline_seconds']:.1f}s rss={res['peak_rss_mb']:.0f}MB "
            f"n={res['n_pages']}"
        )
        hw = out["handwriting"].get(engine, {})
        if hw.get("n_refs"):
            lines.append(f"handwriting N={hw['n_refs']} mean_NED={hw['mean_normalised_edit_distance']:.3f}")
        else:
            lines.append(f"handwriting: {hw.get('note', 'no refs')}")
        if res.get("errors"):
            lines.append(f"errors: {len(res['errors'])} (see JSON)")
    (out_dir / "benchmark_ocr.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
