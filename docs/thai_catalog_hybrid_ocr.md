# Thai catalogs + CPU hybrid OCR (opt-in)

> Status: opt-in, Tesseract remains the default while benchmarking.
> No accuracy claims until `benchmark_ocr.py` reports real gains.
> Last updated: 2026-09-12.

Supports Thai + English **printed** documents across `invoice`,
`purchase_order`, `delivery_note`, with TrOCR assistance for **uncertain
English handwriting** only. English field keys stay canonical; Thai source
values stay verbatim (no translation).

## 1. Catalog changes

Every field in all three catalogs carries optional display-only Thai metadata:

```json
{
  "name": "invoice_number",
  "type": "string",
  "required": true,
  "validation_rule": "Non-empty alphanumeric identifier",
  "label_th": "เลขที่ใบแจ้งหนี้",
  "description_th": "เลขเอกสารใบแจ้งหนี้"
}
```

* Files: `api/app/data/knowledge_base/field_catalog/{invoice_fields,po_fields,delivery_note_fields}.json`.
* Pydantic: `FieldDefinition.label_th / description_th` (`app/schemas/documents.py`);
  exposed via `GET /api/templates` (`list_templates` uses `model_dump`).
* Keys, types, `required`, exact-name matching unchanged. Thai metadata is
  **never** an alias lookup:
  `FieldCatalog.lookup` + `build_alternative_name_lookup` match the English
  `name` only (case/underscore normalisation). `label_th` fails the
  `snake_case` gate, so a model echoing a Thai label can never auto-register
  a duplicate field — it is skipped with `invalid snake_case name`.
* Compact prompts: `name (type[, required]) — <Thai description>`:

  ```
  invoice_number (string, required) — เลขเอกสารใบแจ้งหนี้
  total_amount (number, required) — ยอดเงินรวมที่ต้องชำระ
  ```

  The English prefix stays verbatim (existing `test_compact_prompt_is_small`
  substring intact); Thai follows `—` as a display hint. Token cost ≈ +2–4
  tokens/field.
* Extractor rules (`app/agents/extractors.py::_COMMON_RULES`): Thai hints are
  display-only; Thai values + `source_span` quotes stay verbatim, never
  translated; Thai/Buddhist-calendar dates that do not parse stay verbatim
  and trigger review (validator `Unparseable date` → `needs_review`).
* Unknown-field registration unchanged (`register_discovered_fields` +
  evidence gate); Thai display labels cannot create duplicates.
* Out of scope (v1): UI localisation, Thai date/calendar conversion.

## 2. Hybrid OCR (`OCR_ENGINE=hybrid`)

### 2.1 RapidOCR upgrade

Legacy `rapidocr_onnxruntime==1.2.3` (PP-OCRv3, Latin-only) is retired.
`api/app/services/rapidocr_client.py` now uses the maintained **`rapidocr`**
package with **explicit** PP-OCRv5 mobile models on ONNXRuntime CPU — never
package defaults:

| Stage | Engine | Lang | Model | Version |
|---|---|---|---|---|
| Det | ONNXRUNTIME | `CH` (language-agnostic) | MOBILE | PP-OCRv5 |
| Rec TH | ONNXRUNTIME | `TH` (Thai+English) | MOBILE | PP-OCRv5 |
| Rec EN | ONNXRUNTIME | `EN` (English) | MOBILE | PP-OCRv5 |
| Cls | ONNXRUNTIME | `CH` | MOBILE | PP-OCRv5 |

```python
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR
th = RapidOCR(params={
  "Det.engine_type": EngineType.ONNXRUNTIME, "Det.lang_type": LangDet.CH,
  "Det.model_type": ModelType.MOBILE, "Det.ocr_version": OCRVersion.PPOCRV5,
  "Cls.engine_type": EngineType.ONNXRUNTIME, "Cls.lang_type": LangDet.CH,
  "Cls.model_type": ModelType.MOBILE, "Cls.ocr_version": OCRVersion.PPOCRV5,
  "Rec.engine_type": EngineType.ONNXRUNTIME, "Rec.lang_type": LangRec.TH,
  "Rec.model_type": ModelType.MOBILE, "Rec.ocr_version": OCRVersion.PPOCRV5,
})
```

`OCR_ENGINE=rapidocr` now means PP-OCRv5 TH (Thai+English printed).

### 2.2 Orchestration (`app/services/hybrid_ocr.py`)

Per page, CPU sequential:

1. **Detect once** (TH engine `use_det=True, use_cls=True, use_rec=True`);
   retain 4-point polygons.
2. **Crop + correct orientation** per region (`getPerspectiveTransform` +
   `BORDER_REPLICATE` + 90° fix for tall crops; `crop_region` caps giant
   crops; degenerate crops skip retries). Bounding boxes retained
   (axis-aligned in `OCRBlock.box`, polys used for crops).
3. **Thai first; English retry** on non-Thai regions (`use_det=False,
   use_cls=False, use_rec=True` — never re-detect). Thai regions skip EN.
   Conflicts retain **both** readings (`alternatives`) + region review;
   provisional selection prefers EN for non-Thai Latin (EN specialised) —
   never assumes cross-model calibration.
4. **TrOCR retry** (`app/services/trocr_client.py`,
   `microsoft/trocr-base-handwritten`, LINE crops only — never full pages):
   * Eligibility (selective retry, not a handwriting classifier):
     nonempty Latin-containing selected text, **no Thai in either RapidOCR
     candidate**, selected RapidOCR conf < `HYBRID_TROCR_CONF_THRESHOLD`
     (default 0.80). Skips empty, digit-only (no letters), ambiguous-script
     (non-Latin/non-Thai letters, e.g. CJK/Arabic).
   * At most `HYBRID_TROCR_MAX_REGIONS` (default 10) lowest-confidence
     eligible regions per page, sequential CPU.
   * Different nonempty Latin TrOCR reading → provisional use + page review.
     Original + TrOCR readings retained in `alternatives`. Generation scores
     are **never** extraction confidence — original RapidOCR confidence kept.
5. `OCRBlock` list → `layout_text` (row grouping + ` | ` table gaps + Thai
   token joining preserved). Only selected text enters extraction;
   sanitisation (`sanitize_document_text`), evidence validation
   (`check_evidence`), previews, and page separation unchanged. Thai
   handwriting remains **unsupported** as a guaranteed capability.

### 2.3 Contracts + propagation

`app/schemas/ocr.py` (all backward-compatible defaults):

```python
class AlternativeReading(BaseModel):
    text: str; engine: str = ""; confidence: float | None = None
class OCRBlock(BaseModel):
    text: str; confidence: float; box: tuple[float,float,float,float]
    engine: str = "tesseract"  # rapidocr-th | rapidocr-en | trocr
    alternatives: list[AlternativeReading] = []
    review_reason: str | None = None
class OCRPage(BaseModel):
    engine: str = "tesseract"  # tesseract | rapidocr | hybrid
    engines_used: list[str] = []
    model_revisions: dict[str,str] = {}
    review_reasons: list[str] = []
```

`extraction_service._merge_ocr_reviews` prepends `OCR: <reason>` (+ up to 5
region notes) to `validation_errors` and forces `needs_review`. The merge
runs in `extract_group` **after** the page pipeline, so in-process and
Temporal (`ExtractPageWorkflow`) paths share it with no workflow signature
change. `ocr_blocks[].engine/alternatives/review_reason` survive for UI
review; `timings` gains `ocr_engine_<name>=1.0` flags.

Cache fingerprints (`LocalOCRClient`): file bytes + DPI + engine + languages
+ Tesseract hashes + `layout-v5` + `rapid-ppocrv5-th-en-v1` + hybrid dict
(model revisions, threshold/limit, `hybrid-v1` preprocess). Changing models,
settings, or preprocessing invalidates disk + memory caches.

Timeouts/failures: hybrid runs inside `asyncio.wait_for(ocr_timeout)` with a
monotonic deadline checked before each TrOCR crop. Failed TrOCR/EN retries
preserve RapidOCR text + review reason; primary detection failure raises →
existing page-error path (`failed_stage="ocr"`).

Models lazy + reused: RapidOCR TH/EN engines (`_engine_lock` + `_infer_lock`),
TrOCR processor/model (`TrOCRClient`, CPU `eval`, `no_grad`). No per-page
reloads.

## 3. Provisioning

```bash
# Base (tesseract default + rapidocr PP-OCRv5):
pip install rapidocr==3.9.2 onnxruntime==1.29.0 pymupdf pillow numpy opencv-python pyclipper shapely pyyaml

# Hybrid handwriting retry (opt-in only, CPU):
pip install transformers==4.55.4 torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

# Pre-download RapidOCR ONNX models (offline deploys):
rapidocr download_models --config hybrid_rapidocr.yaml
# TrOCR downloads from HuggingFace on first hybrid page, then cached in
# $HF_HOME / ~/.cache/huggingface. For air-gapped hosts, pre-pull:
#   HF_HUB_OFFLINE=0 python -c "from transformers import TrOCRProcessor, VisionEncoderDecoderModel as M; \
#     TrOCRProcessor.from_pretrained('microsoft/trocr-base-handwritten'); \
#     M.from_pretrained('microsoft/trocr-base-handwritten')"
```

Tesseract stays the default: no new provisioning for `tesseract`/`rapidocr`
paths beyond the `rapidocr` pip upgrade. Missing hybrid deps produce explicit
`RapidOCRError`/review reasons, never silent wrong text.

## 4. Dependency compatibility

| Dep | Tested pin | Notes |
|---|---|---|
| `rapidocr` | `==3.9.2` | Maintained package; `rapidocr_onnxruntime` retired |
| `onnxruntime` | `==1.29.0` | CPU; x86_64 + arm64 |
| `opencv-python` | `>=4.8` | Perspective crops |
| `pyclipper/shapely/pyyaml` | as in requirements | RapidOCR runtime |
| `transformers` | `==4.55.4` | Hybrid only |
| `torch` | `==2.8.0` (CPU) | Hybrid only; ~2 GB; sequential |
| `microsoft/trocr-base-handwritten` | rev `aff187bd81f8d73231cd3ed24b7857fcb10ae00e` | ViT+RoBERTa, IAM; line-only |
| Python | 3.14 | Repo toolchain |
| PP-OCRv5 mobile | Det CH, Rec TH/EN, Cls CH | ModelScope auto-download |

If `rapidocr` import fails, `OCR_ENGINE=rapidocr|hybrid` pages error with an
install hint (existing page-error path). TrOCR load failure → RapidOCR text +
`hybrid: trocr unavailable` review (extraction still completes).

## 5. Configuration

| Var | Default | Effect |
|---|---|---|
| `OCR_ENGINE` | `tesseract` | `tesseract` \| `rapidocr` \| `hybrid` (opt-in) |
| `OCR_LANGUAGES` | `eng+tha` | Tesseract langs (hybrid ignores; uses TH/EN models) |
| `OCR_TIMEOUT_SECONDS` | `120` | Bounds **all** engines incl. hybrid |
| `HYBRID_TROCR_CONF_THRESHOLD` | `0.80` | RapidOCR conf below → TrOCR-eligible |
| `HYBRID_TROCR_MAX_REGIONS` | `10` | Max TrOCR line crops/page (lowest-conf first) |
| `HYBRID_TROCR_MODEL` | `microsoft/trocr-base-handwritten` | Line recogniser only |
| `HYBRID_TROCR_REVISION` | `aff187bd…` | Pinned HF revision |
| `HYBRID_PREPROCESS_VERSION` | `hybrid-v1` | Bump invalidates OCR cache |

## 6. Limitations

* Printed Thai/English only as guaranteed; English handwriting is
  **assistance** (provisional + review), Thai handwriting unsupported.
* Heavy cursive/scribbles, tiny (<8 px), skewed photos, blank forms → low
  recall → `needs_review`, never hallucinations.
* TrOCR IAM domain: neat Latin handwriting best; digits-only skipped.
* CPU latency: RapidOCR ~1 s/page; +EN retry ~+0.3 s; TrOCR ~1–3 s/crop CPU
  (≤10 crops bounded by `OCR_TIMEOUT_SECONDS`). Memory +~1.5 GB when TrOCR
  resident.
* Confidences across TH/EN/TrOCR are **not** comparable; conflicts always
  flag review.
* No UI localisation; no Thai calendar conversion (unsupported dates → review).

## 7. Rollback

```bash
# Instant: no code change, no redeploy of models.
OCR_ENGINE=tesseract ./scripts/run_all.sh
# Optional: remove hybrid deps, revert requirements.
pip uninstall -y transformers torch rapidocr && pip install rapidocr_onnxruntime==1.2.3
rm -rf .cache/ocr-results  # force re-OCR under the old engine
```

Cache keys include the engine + hybrid fingerprint, so rollback never serves
hybrid text as Tesseract text.

## 8. Validation + rollout

* Tests: `test_thai_catalog_hybrid.py` (Thai round-trips, unchanged keys,
  exact matching, unknown-field registration, mixed pages, table geometry,
  eligibility/limits, conflicts, timeouts, cache invalidation, review
  propagation — all mocked, no model downloads).
* Benchmark (unchanged LLM settings, identical references):
  `api/scripts/benchmark_ocr.py --engines tesseract,rapidocr,hybrid --gold-dir
  api/app/data/knowledge_base/ground_truth`. Reports field accuracy,
  table-cell accuracy, handwriting transcription errors, review rate, CPU page
  latency, peak RSS. Existing gold lacks handwriting → add manually verified
  English handwriting references under `ground_truth/handwriting/` before
  claiming gains (see script `--help`).
* CI: `ruff check api/ && python -m pytest api/tests/ -q` + `cd web && npm run build`.
* Keep `hybrid` opt-in; do not claim improved accuracy until measured.
