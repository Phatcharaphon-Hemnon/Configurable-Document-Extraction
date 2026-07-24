from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

from app.agents.extractors import ExtractionContext, OpenSchemaExtractor
from app.agents.judge import JudgeAgent
from app.agents.router import RouterAgent, _normalize_name
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.services.field_aliases import resolve_field_alias
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateResponse,
    ExtractionResult,
    FileExtractionResponse,
    FileUploadMeta,
    ValidationResult,
)
# BUGFIX: this used to import from the deleted `gemini_client` module
# (removed when we switched providers to SUT GenAI). All other files already
# import from sut_genai_client — this file had drifted out of sync.
from app.services.sut_genai_client import SutGenAICallError as GeminiCallError, SutGenAIClient as GeminiClient
from app.services.job_store import InMemoryJobStore
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.llamaparse_client import LlamaParseClient

# Image file extensions and MIME types that GPT-4.1 can process directly
# via vision, skipping the LlamaParse OCR step entirely.
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
_IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
}


def _is_image_file(filename: str, content_type: str | None) -> bool:
    """Return True if this upload looks like an image the vision API can handle."""
    if content_type and content_type.lower() in _IMAGE_CONTENT_TYPES:
        return True
    ext = Path(filename).suffix.lower()
    return ext in _IMAGE_EXTENSIONS


def _image_media_type(filename: str, content_type: str | None) -> str:
    """Return a suitable MIME type for the image."""
    if content_type and content_type.lower() in _IMAGE_CONTENT_TYPES:
        return content_type.lower()
    ext = Path(filename).suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(ext, "image/jpeg")


class UploadedFilePart:
    """One raw uploaded file, before it's been split into page images."""

    def __init__(self, filename: str, content_type: str | None, raw_content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.raw_content = raw_content


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))
        self.llamaparse = LlamaParseClient(settings.llama_cloud_api_key)
        self.router = RouterAgent(
            settings,
            knowledge_base=self.knowledge_base,
            schema_mode=settings.schema_mode,
        )
        self.extractor = OpenSchemaExtractor(settings)
        self.validator = ValidatorAgent()
        self.judge = JudgeAgent(settings)
        self.job_store = job_store or InMemoryJobStore()

    def list_templates(self) -> list[Any]:
        return self.knowledge_base.list_templates()

    def _values_match(self, predicted: Any, expected: Any) -> bool:
        """Compare prediction vs ground-truth values with normalization.

        This affects only the *match decision* (precision/recall/F1 and
        mismatch classification), not the raw values shown in mismatches.
        """

        # a) Exact-match fast path
        try:
            if predicted == expected:
                return True
        except Exception:
            # e.g., uncomparable types; fall through to normalization
            pass

        # b) None/missing handling
        if predicted is None or expected is None:
            return predicted is None and expected is None

        # Helper: normalize numeric-like strings
        def _try_parse_number(v: Any) -> float | None:
            if v is None:
                return None

            if isinstance(v, (int, float)):
                return float(v)

            if isinstance(v, str):
                s = v.strip()
                # strip common currency symbols and separators
                for sym in ("$", "€", "฿"):
                    s = s.replace(sym, "")
                s = s.replace(",", "")
                # allow things like "(1,234.56)" to mean negative
                if s.startswith("(") and s.endswith(")"):
                    s = "-" + s[1:-1]
                try:
                    return float(s)
                except ValueError:
                    return None
            return None

        # c) Numeric-like comparison (with epsilon)
        pn = _try_parse_number(predicted)
        en = _try_parse_number(expected)
        if pn is not None and en is not None:
            return abs(pn - en) <= 1e-6

        # e) Date-like comparison
        from datetime import datetime
        import re

        def _try_parse_date(v: Any) -> datetime | None:
            if not isinstance(v, str):
                return None
            s = v.strip()
            if not s:
                return None

            # If it's a plain number string, don't treat as date
            if re.fullmatch(r"\d+", s):
                return None

            formats = [
                "%d/%m/%y",
                "%d/%m/%Y",
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%B %d, %Y",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        pd = _try_parse_date(predicted)
        ed = _try_parse_date(expected)
        if pd is not None and ed is not None:
            return pd == ed

        # f) List/array comparison (JSON-encoded strings)
        def _try_parse_json_container(v: Any) -> Any | None:
            if isinstance(v, (list, dict)):
                return v
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return None
                # only attempt json parsing if it looks like a container
                if not (s.startswith("[") or s.startswith("{")):
                    return None
                try:
                    return json.loads(s)
                except Exception:
                    return None
            return None

        p_container = _try_parse_json_container(predicted)
        e_container = _try_parse_json_container(expected)
        if p_container is not None and e_container is not None:
            # Both containers recognized: compare recursively
            if isinstance(p_container, list) and isinstance(e_container, list):
                if len(p_container) != len(e_container):
                    return False
                return all(self._values_match(p_item, e_item) for p_item, e_item in zip(p_container, e_container))
            if isinstance(p_container, dict) and isinstance(e_container, dict):
                if p_container.keys() != e_container.keys():
                    return False
                return all(self._values_match(p_container[k], e_container[k]) for k in e_container.keys())
            # One list and one dict (or container types mismatch)
            return False

        # d) String comparison
        if isinstance(predicted, str) and isinstance(expected, str):
            def _normalize_ws(s: str) -> str:
                s = s.strip()
                s = re.sub(r"\s+", " ", s)
                return s

            return _normalize_ws(predicted).lower() == _normalize_ws(expected).lower()

        # g) Fallback
        try:
            return predicted == expected
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Field-name reconciliation for evaluation
    # ------------------------------------------------------------------

    def _reconcile_field_names(
        self,
        prediction: dict[str, Any],
        ground_truth: dict[str, Any],
        doc_type: str,
    ) -> dict[str, Any]:
        """Rename prediction keys to match ground-truth canonical names.

        Builds a lookup of ground-truth keys using both
        :func:`_normalize_name` and :func:`resolve_field_alias` so that
        semantic synonyms are resolved.  Prediction keys that match a
        ground-truth key (after normalization + alias resolution) are
        renamed to the ground-truth-canonical form.  Keys with no match
        are left untouched.

        The original *prediction* dict is **not** mutated; a new dict is
        returned.
        """
        # Map normalized+aliased ground-truth key → original GT key
        gt_lookup: dict[str, str] = {}
        for gt_key in ground_truth:
            norm = _normalize_name(gt_key)
            resolved = resolve_field_alias(doc_type, norm)
            gt_lookup[resolved] = gt_key
            # Also store the raw normalized form so an exact normalized
            # match (before alias) still works.
            if norm != resolved:
                gt_lookup[norm] = gt_key

        reconciled: dict[str, Any] = {}
        for pred_key, pred_value in prediction.items():
            norm = _normalize_name(pred_key)
            resolved = resolve_field_alias(doc_type, norm)
            if resolved in gt_lookup:
                canonical = gt_lookup[resolved]
            elif norm in gt_lookup:
                canonical = gt_lookup[norm]
            else:
                canonical = pred_key  # no match — keep original
            reconciled[canonical] = pred_value
        return reconciled

    def evaluate(
        self,
        prediction: dict[str, Any],
        ground_truth: dict[str, Any],
        source_text: str | None = None,
        doc_type: str | None = None,
    ) -> EvaluateResponse:
        """Score *prediction* against *ground_truth*.

        Parameters
        ----------
        prediction:
            Extracted field-name → value mapping.
        ground_truth:
            Expected field-name → value mapping.
        source_text:
            Optional source document text (reserved for future use).
        doc_type:
            Optional document type.  When provided, prediction keys are
            reconciled with ground-truth keys via the field-alias table
            before scoring.  When ``None``, exact key matching is used
            (backward-compatible behaviour).
        """
        if doc_type is not None:
            prediction = self._reconcile_field_names(prediction, ground_truth, doc_type)

        matched_fields = 0
        false_positives = 0
        false_negatives = 0
        mismatches = [
            {"field": field_name, "predicted": prediction.get(field_name), "expected": expected_value}
            for field_name, expected_value in ground_truth.items()
            if not self._values_match(prediction.get(field_name), expected_value)
        ]

        for field_name, expected_value in ground_truth.items():
            if self._values_match(prediction.get(field_name), expected_value):
                matched_fields += 1
            else:
                false_negatives += 1

        for field_name, predicted_value in prediction.items():
            if not self._values_match(predicted_value, ground_truth.get(field_name)):
                false_positives += 1

        precision = matched_fields / (matched_fields + false_positives) if (matched_fields + false_positives) else 1.0
        recall = matched_fields / (matched_fields + false_negatives) if (matched_fields + false_negatives) else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        return EvaluateResponse(
            score=f1,
            precision=precision,
            recall=recall,
            f1=f1,
            summary="Evaluation completed",
            mismatches=mismatches,
        )

    # ------------------------------------------------------------------
    # Multi-file / multi-page extraction
    # ------------------------------------------------------------------

    async def extract_group(self, parts: list[UploadedFilePart]) -> FileExtractionResponse:
        """Process a GROUP of uploaded files as pages of ONE logical upload.

        Every incoming file must go through LlamaParse first — UNLESS the
        file is an image.  Image files are sent directly to GPT-4.1 via
        the vision API, skipping LlamaParse entirely for speed & accuracy.
        """
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        # --- Fast path: all parts are images → skip LlamaParse entirely ---
        all_images = all(_is_image_file(p.filename, p.content_type) for p in parts)

        if all_images:
            logger.info(
                "All %d file(s) are images — using direct vision path (skipping LlamaParse)",
                len(parts),
            )
            if len(parts) == 1:
                documents = [
                    await self._extract_one_page(
                        filename=combined_name,
                        page_text="",
                        image_bytes=parts[0].raw_content,
                        image_media_type=_image_media_type(parts[0].filename, parts[0].content_type),
                    )
                ]
            else:
                documents = list(
                    await asyncio.gather(*[
                        self._extract_one_page(
                            filename=combined_name,
                            page_text="",
                            image_bytes=p.raw_content,
                            image_media_type=_image_media_type(p.filename, p.content_type),
                        )
                        for p in parts
                    ])
                )
            return FileExtractionResponse(request=request_meta, documents=documents)

        # --- Standard path: non-image files go through LlamaParse ---
        pages_text: list[str] = []
        try:
            if len(parts) == 1:
                parsed_pages = await self.llamaparse.aparse_file(parts[0].raw_content, parts[0].filename)
                pages_text.extend(parsed_pages)
            else:
                parse_results = await asyncio.gather(*[
                    self.llamaparse.aparse_file(part.raw_content, part.filename)
                    for part in parts
                ])
                for parsed_pages in parse_results:
                    pages_text.extend(parsed_pages)
        except Exception as exc:
            return FileExtractionResponse(request=request_meta, error=f"Failed to parse uploaded file(s): {exc}")

        if not pages_text:
            return FileExtractionResponse(
                request=request_meta,
                error="No readable pages/text found in the uploaded file(s).",
            )

        if len(pages_text) == 1:
            documents = [
                await self._extract_one_page(
                    filename=combined_name,
                    page_text=pages_text[0],
                )
            ]
        else:
            documents = list(
                await asyncio.gather(*[
                    self._extract_one_page(
                        filename=combined_name,
                        page_text=pt,
                    )
                    for pt in pages_text
                ])
            )

        return FileExtractionResponse(request=request_meta, documents=documents)

    async def _extract_one_page(
        self,
        filename: str,
        page_text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
    ) -> ExtractionResult:
        try:
            routing = await self.router.classify(
                filename=filename,
                text_hint=page_text or None,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )

            # --- Few-shot injection (opt-out via FEW_SHOT_EXAMPLES_PER_DOC_TYPE=0) ---
            few_shot: list[dict] | None = None
            limit = self.settings.few_shot_examples_per_doc_type
            if limit > 0:
                few_shot = self.knowledge_base.get_few_shot_examples(
                    routing.doc_type, limit=limit
                ) or None  # normalise [] → None so the extractor skips the block

            context = ExtractionContext(
                text=page_text,
                doc_type=routing.doc_type,
                suggested_fields=routing.suggested_fields,
                metadata={"filename": filename},
                few_shot_examples=few_shot,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )
            extracted_fields, additional_fields = await self.extractor.extract(context)

            catalog_fields = self.knowledge_base.get_catalog_fields(routing.doc_type)
            validation = self.validator.validate(
                suggested_fields=routing.suggested_fields,
                extracted_fields=extracted_fields,
                additional_fields=additional_fields,
                schema_mode=self.settings.schema_mode,
                catalog_fields=catalog_fields,
            )
            judge_result = await self.judge.evaluate(
                prediction={
                    **{name: f.value for name, f in extracted_fields.items()},
                    **{name: f.value for name, f in additional_fields.items()},
                },
                source_text=page_text or None,
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )

            # --- Auto-evaluation against ground truth (local dict comparison only) ---
            auto_eval = None
            filename_stem = Path(filename).stem
            gt = self.knowledge_base.get_ground_truth(filename_stem)
            if gt is not None:
                prediction_flat = {
                    **{name: f.value for name, f in extracted_fields.items()},
                    **{name: f.value for name, f in additional_fields.items()},
                }
                auto_eval = self.evaluate(prediction=prediction_flat, ground_truth=gt, doc_type=routing.doc_type)

            return ExtractionResult(
                doc_type=routing.doc_type,
                language=routing.language,
                routing_reason=routing.reason,
                suggested_fields=routing.suggested_fields,
                extracted_fields=extracted_fields,
                additional_fields=additional_fields,
                full_text=page_text or None,
                validation=validation,
                judge=judge_result,
                needs_review=(
                    (not validation.is_valid)
                    or (judge_result.score < 0.7)
                    or (self.settings.schema_mode == "strict" and getattr(routing, "out_of_catalog", False))
                ),
                auto_evaluation=auto_eval,
            )
        except GeminiCallError as exc:
            return ExtractionResult(
                needs_review=True,
                error=str(exc),
                validation=ValidationResult(is_valid=False, completeness_score=0.0, issues=[]),
            )
        except Exception as exc:
            return ExtractionResult(
                needs_review=True,
                error=f"Extraction pipeline failed: {exc}",
                validation=ValidationResult(is_valid=False, completeness_score=0.0, issues=[]),
            )

    # ------------------------------------------------------------------
    # Batch jobs (unchanged shape, now returns FileExtractionResponse)
    # ------------------------------------------------------------------

    def create_batch(self) -> BatchCreateResponse:
        job = self.job_store.create()
        return BatchCreateResponse(job_id=job.job_id, status=job.status)

    def get_batch_status(self, job_id):
        job = self.job_store.get(job_id)
        if job is None:
            return None
        result = None
        if job.result is not None:
            result = FileExtractionResponse.model_validate(job.result)
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result)