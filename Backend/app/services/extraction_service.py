from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.extractors import ExtractionContext, OpenSchemaExtractor
from app.agents.judge import JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
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

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - surfaced clearly at call time instead
    fitz = None


class UploadedFilePart:
    """One raw uploaded file, before it's been split into page images."""

    def __init__(self, filename: str, content_type: str | None, raw_content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.raw_content = raw_content


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self.router = RouterAgent(settings)
        self.extractor = OpenSchemaExtractor(settings)
        self.validator = ValidatorAgent()
        self.judge = JudgeAgent(settings)
        self.job_store = job_store or InMemoryJobStore()
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))

    def list_templates(self) -> list[Any]:
        return self.knowledge_base.list_templates()

    def evaluate(self, prediction: dict[str, Any], ground_truth: dict[str, Any], source_text: str | None = None) -> EvaluateResponse:
        matched_fields = 0
        false_positives = 0
        false_negatives = 0
        mismatches = [
            {"field": field_name, "predicted": prediction.get(field_name), "expected": expected_value}
            for field_name, expected_value in ground_truth.items()
            if prediction.get(field_name) != expected_value
        ]

        for field_name, expected_value in ground_truth.items():
            if prediction.get(field_name) == expected_value:
                matched_fields += 1
            else:
                false_negatives += 1

        for field_name, predicted_value in prediction.items():
            if ground_truth.get(field_name) != predicted_value:
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

    def extract_group(self, parts: list[UploadedFilePart]) -> FileExtractionResponse:
        """Process a GROUP of uploaded files as pages of ONE logical upload.

        - If a part is a PDF, it is split into one page-image per PDF page.
        - If a part is a plain image, it is treated as a single page.
        - All pages from all parts in the group are flattened, in order, and
          each page runs independently through Router -> Extractor ->
          Validator -> Judge, producing one ExtractionResult per page.

        This covers both requested cases: a single multi-page PDF, and the
        user selecting multiple image files together in one upload action
        (e.g. page1.jpg + page2.jpg of the same document).
        """
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        try:
            page_images = self._collect_page_images(parts)
        except Exception as exc:
            return FileExtractionResponse(request=request_meta, error=f"Failed to read uploaded file(s): {exc}")

        if not page_images:
            return FileExtractionResponse(
                request=request_meta,
                error="No readable pages/images found in the uploaded file(s).",
            )

        documents: list[ExtractionResult] = []
        for image_bytes, image_mime_type in page_images:
            documents.append(
                self._extract_one_page(
                    filename=combined_name,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                )
            )

        return FileExtractionResponse(request=request_meta, documents=documents)

    def _collect_page_images(self, parts: list[UploadedFilePart]) -> list[tuple[bytes, str]]:
        pages: list[tuple[bytes, str]] = []
        for part in parts:
            if self._is_pdf(part.filename, part.content_type):
                pages.extend(self._pdf_to_page_images(part.raw_content))
            elif self._is_image_upload(part.filename, part.content_type):
                pages.append((part.raw_content, part.content_type or "image/png"))
            else:
                raise GeminiCallError(
                    f"Unsupported file type for '{part.filename}' — only PDF and common image "
                    "formats (png/jpg/webp/etc.) are supported."
                )
        return pages

    def _pdf_to_page_images(self, pdf_bytes: bytes, dpi: int = 200) -> list[tuple[bytes, str]]:
        if fitz is None:
            raise GeminiCallError(
                "PDF support requires the 'pymupdf' package, which is not installed. "
                "Add pymupdf to requirements.txt and reinstall dependencies."
            )
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            zoom = dpi / 72
            matrix = fitz.Matrix(zoom, zoom)
            pages: list[tuple[bytes, str]] = []
            for page in doc:
                pixmap = page.get_pixmap(matrix=matrix)
                pages.append((pixmap.tobytes("png"), "image/png"))
            return pages
        finally:
            doc.close()

    def _extract_one_page(self, filename: str, image_bytes: bytes, image_mime_type: str) -> ExtractionResult:
        final_text = ""
        try:
            final_text = self._extract_text_from_image(image_bytes=image_bytes, mime_type=image_mime_type) or ""
        except GeminiCallError as exc:
            # TEMP DEBUG — remove after bug is found
            print(f"[TEMP DEBUG] _extract_text_from_image GeminiCallError: {exc}")
            final_text = ""

        try:
            routing = self.router.classify(
                filename=filename,
                content_type=image_mime_type,
                text_hint=final_text or None,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )

            # --- Few-shot injection (opt-out via FEW_SHOT_EXAMPLES_PER_DOC_TYPE=0) ---
            few_shot: list[dict] | None = None
            limit = self.settings.few_shot_examples_per_doc_type
            if limit > 0:
                few_shot = self.knowledge_base.get_few_shot_examples(
                    routing.doc_type, limit=limit
                ) or None  # normalise [] → None so the extractor skips the block

            context = ExtractionContext(
                text=final_text,
                doc_type=routing.doc_type,
                suggested_fields=routing.suggested_fields,
                metadata={"filename": filename, "content_type": image_mime_type},
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
                few_shot_examples=few_shot,
            )
            extracted_fields, additional_fields = self.extractor.extract(context)

            validation = self.validator.validate(
                suggested_fields=routing.suggested_fields,
                extracted_fields=extracted_fields,
                additional_fields=additional_fields,
            )
            judge_result = self.judge.evaluate(
                prediction={
                    **{name: f.value for name, f in extracted_fields.items()},
                    **{name: f.value for name, f in additional_fields.items()},
                },
                source_text=final_text or None,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
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
                auto_eval = self.evaluate(prediction=prediction_flat, ground_truth=gt)

            return ExtractionResult(
                doc_type=routing.doc_type,
                language=routing.language,
                routing_reason=routing.reason,
                suggested_fields=routing.suggested_fields,
                extracted_fields=extracted_fields,
                additional_fields=additional_fields,
                full_text=final_text or None,
                validation=validation,
                judge=judge_result,
                needs_review=(not validation.is_valid) or (judge_result.score < 0.7),
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

    def _is_pdf(self, filename: str, content_type: str | None) -> bool:
        if content_type == "application/pdf":
            return True
        return filename.lower().endswith(".pdf")

    def _is_image_upload(self, filename: str, content_type: str | None) -> bool:
        lower_name = filename.lower()
        if content_type is not None and content_type.startswith("image/"):
            return True
        return lower_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"))

    def _extract_text_from_image(self, image_bytes: bytes, mime_type: str | None) -> str | None:
        client = GeminiClient(self.settings)
        result = client.generate_text(
            model=self.settings.recommended_extraction_model_name,
            prompt=(
                "Extract all readable text from this document image. "
                "Return only the text content, preserve line breaks, and do not add commentary."
            ),
            image_bytes=image_bytes,
            image_mime_type=mime_type,
        )
        return result.raw_text

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