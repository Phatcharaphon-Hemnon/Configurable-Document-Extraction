"""Extraction orchestration: upload → local OCR → Router → Extractor →
Validator → Judge → (auto-eval). One document per result; multi-page and
multi-document uploads produce one result per page/document."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.extractors import build_extractors
from app.agents.judge import JUDGE_PASS_SCORE, JudgeAgent
from app.agents.router import RouterAgent
from app.agents.validator import ValidatorAgent
from app.core.config import Settings
from app.core.security import is_ocr_text_coherent, is_verbatim_span, sanitize_document_text
from app.guards.audit_logger import AuditLogger
from app.guards.content_guard import ContentLimits, validate_document_text
from app.guards.pii_detector import PIIDetector
from app.guards.timeout_guard import StageTimeoutConfig, TimeoutGuard, stage_notifier
from app.observability.langfuse import LangfuseTracer
from app.prompts.registry import active_versions as active_prompt_versions
from app.prompts.registry import compound_prompt_version
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateResponse,
    ExtractedField,
    ExtractionResult,
    FileExtractionResponse,
    FileUploadMeta,
    ProviderErrorDetails,
)
from app.services.client import ClientError
from app.services.field_catalog import register_discovered_fields
from app.services.field_matching import values_match
from app.services.job_store import InMemoryJobStore, JobRecord, SQLiteJobStore
from app.services.knowledge_base import KnowledgeBaseRepository
from app.services.local_ocr import LocalOCRClient, OCRPage
from app.services.request_control import job_context
from app.services.source_storage import SourceStorage

logger = logging.getLogger(__name__)
queue_wait: ContextVar[float] = ContextVar("queue_wait", default=0)


def _judge_demands_review(judge_result) -> bool:
    """True when the judge reports a real problem.

    `info`-severity issues are confirmations ("value X is supported"),
    not problems — only `warning`/`error` issues or a sub-threshold score
    keep a page in review.
    """
    if judge_result is None:
        return False
    if judge_result.score < JUDGE_PASS_SCORE:
        return True
    return any(getattr(i, "severity", "warning") in ("warning", "error")
               for i in (judge_result.issues or []))


def _is_numeric_value(value: object) -> bool:
    """True for numbers and decimal-formatted numeric strings (not IDs/dates)."""
    import re as _re

    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(_re.fullmatch(r"-?\d+[,.]\d[\d,.]*", value.strip()))
    return False


def _all_numeric_spans_verbatim(fields: list[ExtractedField], tables: list, page_text: str | None) -> bool:
    """Judge-skip gate: every numeric field/cell span must be a contiguous
    page-text substring. A confidently-wrong number with a paraphrased span
    must always get an independent judge review (never skip)."""
    if not page_text:
        return False
    spans = [
        (f.value, f.source_span)
        for f in fields
        if f.value is not None and f.source_span
    ]
    for table in tables or []:
        for row in table.rows:
            for cell in row:
                if cell.value is not None and cell.source_span:
                    spans.append((cell.value, cell.source_span))
    for value, span in spans:
        if _is_numeric_value(value) and not is_verbatim_span(span, page_text):
            return False
    return True


def _merge_ocr_reviews(document: ExtractionResult, ocr_page) -> ExtractionResult:
    """Propagate OCR uncertainty into validation_errors/needs_review.

    Used by BOTH the in-process pipeline and the Temporal path (the merge
    happens in ``extract_group`` after the workflow returns, so no workflow
    signature change is needed). Only the selected text was fed into
    extraction; alternative readings stay in ``ocr_blocks`` for review.
    """
    reasons = list(getattr(ocr_page, "review_reasons", None) or [])
    # Region-level reasons add context without duplicating page reasons.
    try:
        blocks = list(getattr(ocr_page, "blocks", None) or [])
    except Exception:
        blocks = []
    region_notes = []
    for block in blocks:
        reason = getattr(block, "review_reason", None)
        if reason and reason not in region_notes and len(region_notes) < 5:
            region_notes.append(reason)
    # Keep validation_errors readable: page reasons first, then a sample of
    # region notes (full detail remains in ocr_blocks[].review_reason).
    merged = [f"OCR: {r}" for r in reasons if r]
    for note in region_notes:
        candidate = f"OCR region: {note}"
        if candidate not in merged and len(merged) < 8:
            merged.append(candidate)
    existing = set(document.validation_errors)
    merged = [m for m in merged if m not in existing]
    if merged:
        document.validation_errors = [*merged, *document.validation_errors]
        document.needs_review = True
    return document


def _provider_error_details(
    exc: BaseException,
    *,
    stage: str,
    model: str | None,
    provider: str | None,
) -> ProviderErrorDetails | None:
    """Walk the exception chain for ClientError.provider_details.

    Returns None when no provider call was involved (e.g. OCR/validator
    failures) so UI <details> only appears for real gateway errors.
    """
    seen: ProviderErrorDetails | None = None
    current: BaseException | None = exc
    while current is not None:
        details = getattr(current, "provider_details", None)
        if isinstance(details, dict) and details:
            seen = ProviderErrorDetails(
                stage=stage,
                provider=provider,
                model=model,
                error_type=details.get("error_type"),
                status=details.get("status"),
                code=details.get("code"),
                param=details.get("param"),
                type=details.get("type"),
                message=details.get("message"),
                request_id=details.get("request_id"),
            )
            break
        current = current.__cause__ or current.__context__
    if seen is None and isinstance(exc, ClientError):
        # Timeout/budget ClientErrors without SDK body still get context.
        seen = ProviderErrorDetails(stage=stage, provider=provider, model=model)
    return seen


# Router language-tag correction: Tesseract with Thai traineddata emits
# Thai-looking fragments on English print and a small router LLM obeys the
# noise. A page tagged "th" whose letters are mostly Latin is really
# English. Threshold is a letters-only majority vote (digits/symbols
# excluded); only the tag is ever touched.
_LATIN_PAGE_THRESHOLD = 0.70


def _correct_router_language(routing, page_text: str | None):
    """Fix a noisy "th" tag on Latin-majority pages; nothing else changes.

    `doc_type`, confidence, and the LLM's original reason are preserved —
    the correction is appended to the reason for auditability. Pages tagged
    anything other than "th" are returned untouched.
    """
    if getattr(routing, "language", None) != "th":
        return routing
    from app.services.hybrid_ocr import page_latin_fraction

    if page_latin_fraction(page_text) <= _LATIN_PAGE_THRESHOLD:
        return routing
    note = " [language tag corrected th->en: Latin-majority page]"
    return routing.model_copy(
        update={"language": "en", "reason": f"{routing.reason or ''}{note}"}
    )

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
_IMAGE_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff",
}


def _is_image_file(filename: str, content_type: str | None) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in _IMAGE_EXTENSIONS or (content_type in _IMAGE_CONTENT_TYPES)


def _image_media_type(filename: str, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(ext, "image/jpeg")


def _agent_usage(agent: object) -> dict[str, int] | None:
    """Token counts from an agent's last LLM call, for trace generations.

    Read synchronously right after awaiting the agent (same task, no await
    in between) so concurrent pages can't interleave. Returns None for
    mocked agents or calls without usage data.
    """
    usage = getattr(getattr(agent, "_client", None), "last_usage", None)
    if not isinstance(usage, dict):
        return None
    cleaned = {
        key: int(value)
        for key, value in usage.items()
        if isinstance(value, (int, float)) and value is not None
    }
    return cleaned or None


class UploadedFilePart:
    """One raw uploaded file, before it's been split into pages."""

    def __init__(self, filename: str, content_type: str | None, raw_content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.raw_content = raw_content


_VALID_DOC_TYPES = ("invoice", "purchase_order", "delivery_note")


def parse_doc_type(value: str | None) -> str | None:
    """Validate an explicit user-selected document type.

    Returns the normalized type, or None when the caller wants automatic
    Router classification. Raises ValueError for unknown types (surfaced as
    HTTP 400 by the API layer).
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in _VALID_DOC_TYPES:
        raise ValueError(
            f"Unknown doc_type={value!r} — expected one of: {list(_VALID_DOC_TYPES)} "
            "(or omit it for automatic classification)"
        )
    return normalized


def _count_pages(data: bytes, filename: str | None) -> int | None:
    """Cheap page count without OCR (None when undecodable).

    PDFs report the container page count via PyMuPDF (no rendering);
    images report TIFF frame counts, otherwise a single page.
    """
    try:
        from app.services.local_ocr import is_pdf_document

        if is_pdf_document(data, filename):
            import pymupdf

            doc = pymupdf.open(stream=data, filetype="pdf")
            try:
                return len(doc) or None
            finally:
                doc.close()
        import io as _io

        from PIL import Image as _Image

        img = _Image.open(_io.BytesIO(data))
        try:
            count = getattr(img, "n_frames", 1) if img.format == "TIFF" else 1
            return int(count) or None
        finally:
            try:
                img.close()
            except Exception:
                pass
    except Exception:
        return None
    return None


def _render_previews(data: bytes, filename: str | None, dpi: int) -> list[bytes] | None:
    """Render-only page previews (no OCR, no models) for cache fast-path hits.

    Mirrors the OCR path's preview bytes (thumbnail PNG) so a fast-path
    submission carries the same preview/download references as a normal one.
    Returns None when the file cannot be rendered.
    """
    try:
        import io as _io

        from app.services.local_ocr import load_page_images

        previews: list[bytes] = []
        for loaded in load_page_images(data, filename, dpi):
            thumb = loaded.image.copy()
            thumb.thumbnail((1500, 2000))
            buffer = _io.BytesIO()
            thumb.save(buffer, format="PNG")
            previews.append(buffer.getvalue())
        return previews or None
    except Exception:
        return None


class DocumentExtractionService:
    def __init__(self, settings: Settings, job_store: InMemoryJobStore | None = None) -> None:
        self.settings = settings
        self._job_lock = asyncio.Lock()
        self.knowledge_base = KnowledgeBaseRepository(Path(settings.knowledge_base_path))
        self.catalog = self.knowledge_base.catalog
        # Local OCR (RapidOCR, ONNX): handles both PDFs (rendered at OCR_DPI
        # via PyMuPDF) and images. Runs fully offline — no API key, no network.
        self.ocr = LocalOCRClient(settings)
        self.sources = SourceStorage(settings.source_storage_path)
        self.tracer = LangfuseTracer(settings)
        self.router = RouterAgent(settings)
        self.extractors = build_extractors(settings, self.catalog)
        self.validator = ValidatorAgent(self.catalog)
        self.judge = JudgeAgent(settings)
        # Persistent completed-result cache (SQLite, separate from OCR cache).
        try:
            from app.services.result_cache import ResultCache

            self.result_cache = ResultCache(settings)
        except Exception as exc:
            logger.warning("Result cache disabled: %s", exc)
            self.result_cache = None

        # Use SQLite job store if enabled, otherwise use in-memory
        if settings.database_enabled:
            self.job_store = SQLiteJobStore(db_path=settings.database_path)
            logger.info("Using SQLite job store: %s", settings.database_path)
        else:
            self.job_store = job_store or InMemoryJobStore()
            logger.info("Using in-memory job store")

        # Boot-cleanup: rows left 'queued' or 'processing' belong to requests killed before
        # saving (server restart / client disconnect) — no live worker can
        # own them, so mark them failed for a truthful History tab.
        try:
            orphaned = self.job_store.fail_stale_queued()
            if orphaned:
                logger.info("Marked %d orphaned pending job(s) as failed", orphaned)
        except Exception as exc:
            logger.warning("Stale-job cleanup skipped: %s", exc)

        # Initialize guards
        self.audit_logger = AuditLogger(
            log_file=settings.audit_log_file if settings.audit_log_enabled else None
        )
        self.timeout_guard = TimeoutGuard(
            StageTimeoutConfig(
                router_seconds=settings.router_timeout_seconds,
                extractor_seconds=settings.extractor_timeout_seconds,
                judge_seconds=settings.judge_timeout_seconds,
                ocr_seconds=settings.ocr_timeout_seconds,
            )
        )
        # Startup guard: a request timeout above a stage limit cancels before
        # the timeout retry can run (observed 200s-Router vs 1000s-request).
        try:
            from app.core.config import validate_timeout_config

            for warning in validate_timeout_config(settings):
                logger.warning("Startup timeout config: %s", warning)
            logger.info(
                "Timeout budget request=%.0fs router=%.0fs extractor=%.0fs judge=%.0fs concurrency=%d model=%s",
                settings.llm_request_timeout_seconds,
                settings.router_timeout_seconds,
                settings.extractor_timeout_seconds,
                settings.judge_timeout_seconds,
                settings.llm_max_concurrent_requests,
                settings.llm_model,
            )
        except Exception:
            pass
        self.pii_detector = PIIDetector(
            redact_pii=settings.pii_redact_in_logs
        )
        self.content_limits = ContentLimits(
            max_document_chars=settings.max_document_chars,
            max_prompt_chars=settings.max_prompt_chars,
        )

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        return self.knowledge_base.list_templates()

    # ------------------------------------------------------------------
    # Evaluation (exact catalog-name matching, no aliases)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        prediction: dict[str, Any],
        ground_truth: dict[str, Any],
        source_text: str | None = None,
        doc_type: str | None = None,
    ) -> EvaluateResponse:
        matched = 0
        false_positives = 0
        false_negatives = 0

        mismatches = [
            {"field": name, "predicted": prediction.get(name), "expected": expected}
            for name, expected in ground_truth.items()
            if not values_match(prediction.get(name), expected)
        ]

        for name, expected in ground_truth.items():
            if values_match(prediction.get(name), expected):
                matched += 1
            else:
                false_negatives += 1

        for name, predicted in prediction.items():
            if not values_match(predicted, ground_truth.get(name)):
                false_positives += 1

        precision = matched / (matched + false_positives) if (matched + false_positives) else 1.0
        recall = matched / (matched + false_negatives) if (matched + false_negatives) else 1.0
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

    async def _stream_ocr_pages(self, part: UploadedFilePart, ocr_use_cache: bool | None):
        """Yield ``(page_index, ocr_page)`` in document order as OCR completes.

        Real OCR clients stream via ``LocalOCRClient.aparse_pages`` so page 1
        can be extracted, checkpointed, and polled before later pages finish
        recognizing. Doubles exposing only ``aparse_file`` (plus optional
        ``last_pages``) fall back to the collecting path wrapped in typed
        per-page records. Whole-file OCR failures propagate for the caller to
        record as file errors; per-page failures arrive as ``ocr_page.error``.
        """
        from app.services.local_ocr import LocalOCRClient

        ocr = self.ocr
        # Stream only on an unstubbed real client. Any instance-level
        # `aparse_file` override (AsyncMock/side_effect doubles in tests) is
        # honored via the collecting fallback so stubs keep working.
        if isinstance(ocr, LocalOCRClient) and "aparse_file" not in ocr.__dict__:
            page_index = 0
            async for ocr_page in ocr.aparse_pages(
                part.raw_content, part.filename, use_cache=ocr_use_cache,
            ):
                page_index += 1
                yield page_index, ocr_page
            return
        parsed = await ocr.aparse_file(
            part.raw_content, part.filename, use_cache=ocr_use_cache,
        )
        details = getattr(ocr, "last_pages", [])
        if not isinstance(details, list) or len(details) != len(parsed):
            details = [OCRPage(text=text) for text in parsed]
        for page_index, (page_text, ocr_page) in enumerate(zip(parsed, details), 1):
            if not getattr(ocr_page, "text", None):
                try:
                    ocr_page.text = page_text
                except Exception:
                    pass
            yield page_index, ocr_page

    async def extract_group(
        self,
        parts: list[UploadedFilePart],
        job_id: UUID | None = None,
        force_refresh: bool = False,
        disable_caches: bool = False,
        doc_type: str | None = None,
    ) -> FileExtractionResponse:
        """Multi-file/page extraction with persistent result caching.

        - `force_refresh=True` bypasses the completed-result cache (OCR cache
          stays enabled).
        - `disable_caches=True` (benchmark/debug) bypasses BOTH result and
          OCR caches.
        - `doc_type` optionally fixes the document type for every page,
          bypassing Router classification (extraction validation still runs).
        """
        doc_type = parse_doc_type(doc_type)
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        # Use the caller's job row when running as a background task
        # (created up-front so the client can poll); otherwise create one.
        if job_id is None:
            job = self.job_store.create(
                filename=combined_name,
                content_type=request_meta.content_type,
                size_bytes=total_size,
            )
        else:
            job = JobRecord(job_id=job_id, status="queued")
        job_id_str = str(job.job_id)

        started = time.perf_counter()
        documents = []
        file_errors = []
        progress = {"completed_pages": 0, "total_pages": 0, "stage": "ocr"}
        cache_hits = 0
        cache_misses = 0
        cache_lookup_ms = 0.0
        first_page_elapsed: float | None = None
        cache_on = bool(
            self.result_cache is not None
            and self.result_cache.enabled_and_ready
            and not force_refresh
            and not disable_caches
        )
        ocr_use_cache: bool | None = False if disable_caches else None
        try:
            for part in parts:
                source_id = self.sources.save(part.filename, part.raw_content, part.content_type)
                self.job_store.set_progress(job.job_id, progress)
                # Cheap page count up front (no OCR) so progress shows the full
                # scope before page 1 finishes; None when undecodable (then the
                # total grows as pages stream in).
                expected_pages = _count_pages(part.raw_content, part.filename)
                count_known = bool(expected_pages)
                if count_known:
                    progress["total_pages"] += int(expected_pages)
                    self.job_store.set_progress(job.job_id, progress)
                pages_seen = 0
                try:
                    async for page_index, ocr_page in self._stream_ocr_pages(part, ocr_use_cache):
                        pages_seen += 1
                        if not count_known:
                            progress["total_pages"] += 1
                        page_text = getattr(ocr_page, "text", None) or ""
                        page_total = int(expected_pages or 0) or page_index
                        source = self.sources.reference(source_id, page_index, page_total, ocr_page.preview)
                        # Stable per-page block IDs before any evidence resolution.
                        try:
                            from app.services.evidence import assign_block_ids

                            assign_block_ids(list(getattr(ocr_page, "blocks", None) or []), page_index)
                        except Exception:
                            pass
                        ocr_uncertain_page = bool(
                            getattr(ocr_page, "review_reasons", None)
                            or any(getattr(b, "review_reason", None) for b in (getattr(ocr_page, "blocks", None) or []))
                        )

                        # --- Persistent result cache (checked BEFORE provider queue).
                        fingerprint = ""
                        cached_hit = None
                        cached_meta: dict = {}
                        lookup_ms = 0.0
                        if cache_on and not (getattr(ocr_page, "error", None) or not (page_text or "").strip()):
                            try:
                                _t0 = time.perf_counter()
                                fingerprint = self.result_cache.fingerprint_page(
                                    file_bytes=part.raw_content,
                                    filename=part.filename,
                                    page_number=page_index,
                                    page_text=page_text,
                                    ocr_engine=getattr(ocr_page, "engine", "") or "",
                                    ocr_languages=self.settings.ocr_languages,
                                    ocr_dpi=self.settings.ocr_dpi,
                                    ocr_model_hashes=getattr(self.ocr, "model_hashes", {}) or {},
                                    hybrid_fingerprint=self.ocr._hybrid_fingerprint(),
                                )
                                cached_hit, cached_meta = self.result_cache.get(fingerprint)
                                lookup_ms = (time.perf_counter() - _t0) * 1000
                                cache_lookup_ms += lookup_ms
                            except Exception as exc:
                                logger.warning("Result cache lookup skipped: %s", exc)
                                cached_hit, cached_meta = None, {}
                        if cached_hit is not None:
                            from uuid import uuid4

                            from app.schemas.documents import ResultCacheMetadata

                            cache_hits += 1
                            document = cached_hit.model_copy(
                                update={
                                    "id": uuid4(),
                                    "source": source,
                                    "ocr_blocks": list(getattr(ocr_page, "blocks", None) or []),
                                    "full_text": page_text,
                                    "cache_metadata": ResultCacheMetadata(
                                        fingerprint=fingerprint,
                                        computed_at=cached_meta.get("computed_at_iso"),
                                        original_timings=dict(cached_meta.get("original_timings", {}) or {}),
                                        acceptance_policy_version=cached_meta.get(
                                            "acceptance_policy", "v1.0.0",
                                        ),
                                        cache_lookup_ms=lookup_ms,
                                        hit_type="full",
                                    ),
                                }
                            )
                            try:
                                document.timings.update(
                                    ocr=ocr_page.seconds,
                                    render=ocr_page.render_seconds,
                                    ocr_cached=float(getattr(ocr_page, "cached", False)),
                                    result_cache_hit=1.0,
                                    result_cache_lookup_ms=lookup_ms,
                                )
                            except Exception:
                                pass
                            try:
                                document = _merge_ocr_reviews(document, ocr_page)
                            except Exception:
                                pass
                            documents.append(document)
                            self.job_store.save_result(job.job_id, FileExtractionResponse(
                                request=request_meta, documents=documents, job_id=job_id_str,
                                file_errors=file_errors).model_dump(mode="json"), status="processing")
                            progress["completed_pages"] += 1
                            self.job_store.set_progress(job.job_id, progress)
                            if first_page_elapsed is None:
                                first_page_elapsed = time.perf_counter() - started
                            continue

                        cache_misses += 1

                        def notify_stage(stage):
                            progress["stage"] = stage
                            self.job_store.set_progress(job.job_id, progress)
                        stage_token = stage_notifier.set(notify_stage)
                        try:
                            if ocr_page.error or not page_text.strip():
                                message = ocr_page.error or "No readable text on this page"
                                document = ExtractionResult(doc_type="invoice", fields=[], needs_review=True,
                                    completeness_score=0, error=message, failed_stage="ocr", validation_errors=[message])
                            elif self.settings.temporal_enabled:
                                from app.temporal.client import get_temporal_client
                                client = await get_temporal_client()
                                raw = await client.execute_workflow("ExtractPageWorkflow", args=[part.filename, page_text],
                                    id=f"{job.job_id}-page-{len(documents) + 1}", task_queue=self.settings.temporal_task_queue)
                                document = ExtractionResult.model_validate(raw)
                            else:
                                document = await self._extract_one_page(
                                    filename=part.filename,
                                    page_text=page_text,
                                    ocr_notes=list(getattr(ocr_page, "review_reasons", None) or []),
                                    page_number=page_index,
                                    blocks=list(getattr(ocr_page, "blocks", None) or []),
                                    ocr_uncertain=ocr_uncertain_page,
                                    doc_type=doc_type,
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            document = ExtractionResult(doc_type="invoice", fields=[], needs_review=True,
                                completeness_score=0, error=f"Page pipeline failed: {exc}", validation_errors=[str(exc)])
                        finally:
                            stage_notifier.reset(stage_token)
                        # Store completed pages (valid partial results cache even
                        # when sibling pages fail; the submission as a whole is
                        # never presented as complete from a partial hit).
                        if cache_on and fingerprint:
                            try:
                                self.result_cache.put(fingerprint, document, meta={
                                    "filename": part.filename,
                                    "page_number": page_index,
                                })
                                # Accumulate the preliminary manifest so a repeat
                                # upload can resolve without OCR (fast path).
                                # Manifest needs the same page count the fast path
                                # looks up; skip it when the count is unknown.
                                try:
                                    if expected_pages:
                                        manifest_key = self.result_cache.manifest_key(
                                            file_bytes=part.raw_content,
                                            filename=part.filename,
                                            page_count=int(expected_pages),
                                        )
                                        self.result_cache.manifest_put(
                                            manifest_key, page_index, fingerprint, int(expected_pages),
                                            page_text=page_text)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        document.source = source
                        document.ocr_blocks = ocr_page.blocks
                        document.full_text = page_text
                        document.timings.update(ocr=ocr_page.seconds, render=ocr_page.render_seconds,
                                                ocr_cached=float(ocr_page.cached))
                        # Engine provenance (backward-compatible: timings flags only;
                        # full detail stays in ocr_blocks[].engine + alternatives).
                        try:
                            engine = getattr(ocr_page, "engine", "") or ""
                            engines_used = list(getattr(ocr_page, "engines_used", None) or [])
                            if engine:
                                document.timings[f"ocr_engine_{engine}"] = 1.0
                                for used in engines_used:
                                    if used and used != engine:
                                        document.timings[f"ocr_engine_{used}"] = 1.0
                        except Exception:
                            pass
                        # OCR uncertainty → validation_errors/needs_review (both
                        # in-process and Temporal paths — merge happens here).
                        try:
                            document = _merge_ocr_reviews(document, ocr_page)
                        except Exception:
                            pass
                        documents.append(document)
                        self.job_store.save_result(job.job_id, FileExtractionResponse(
                            request=request_meta, documents=documents, job_id=job_id_str,
                            file_errors=file_errors).model_dump(mode="json"), status="processing")
                        progress["completed_pages"] += 1
                        self.job_store.set_progress(job.job_id, progress)
                        if first_page_elapsed is None:
                            first_page_elapsed = time.perf_counter() - started
                except Exception as exc:
                    # A failure before the first page streamed is an OCR/file
                    # failure for this part (later pages keep prior behavior:
                    # per-page errors become error documents, unexpected bugs
                    # abort honestly instead of masquerading as OCR issues).
                    if pages_seen == 0:
                        file_errors.append(f"{part.filename}: Failed to OCR: {exc}")
                        continue
                    raise
            # Full-hit (all pages cached) / partial-hit / miss, derived from
            # per-page counts. Lookup/remap latency reported separately from
            # original computation timings (preserved in cache_metadata).
            total_pages = cache_hits + cache_misses
            if not cache_on:
                cache_status = "disabled" if self.result_cache is None or not self.result_cache.enabled else "bypassed"
            elif total_pages and cache_hits == total_pages:
                cache_status = "full"
            elif cache_hits:
                cache_status = "partial"
            else:
                cache_status = "miss"
            logger.info(
                "Result cache %s: hits=%d misses=%d lookup_ms=%.1f",
                cache_status, cache_hits, cache_misses, cache_lookup_ms,
            )
            response = FileExtractionResponse(request=request_meta, documents=documents, job_id=job_id_str,
                file_errors=file_errors, error="; ".join(file_errors) if not documents else None,
                timings={"processing": time.perf_counter() - started, "queue": queue_wait.get(),
                         "result_cache_hits": float(cache_hits), "result_cache_misses": float(cache_misses),
                         "result_cache_lookup_ms": cache_lookup_ms,
                         **({"time_to_first_page": first_page_elapsed} if first_page_elapsed is not None else {})})
            if not documents and not response.error:
                response.error = "No readable pages found"
            progress["stage"] = "completed" if documents else "failed"
            self.job_store.set_progress(job.job_id, progress)
            self.job_store.save_result(job.job_id, response.model_dump(mode="json"))
            return response
        except asyncio.CancelledError:
            self.job_store.fail_job(job.job_id, "Cancelled (server shutdown)")
            raise
        except Exception as exc:
            self.job_store.fail_job(job.job_id, str(exc))
            raise

    # ------------------------------------------------------------------
    # Single page pipeline
    # ------------------------------------------------------------------

    async def _extract_one_page(
        self,
        filename: str,
        page_text: str,
        ocr_notes: list[str] | None = None,
        page_number: int = 1,
        blocks: list | None = None,
        ocr_uncertain: bool = False,
        **kwargs,
    ) -> ExtractionResult:
        from app.guards.timeout_guard import page_timings
        measured: dict[str, float] = {}
        token = page_timings.set(measured)
        started = time.perf_counter()
        agents = {"router": self.router, "judge": self.judge}
        agents.update({f"extractor_{key}": agent for key, agent in self.extractors.items()})
        for agent in agents.values():
            client = getattr(agent, "_client", None)
            if client is not None:
                client.last_usage = None
                client.last_attempts = 0
        try:
            result = await self._extract_page_impl(
                filename,
                page_text,
                ocr_notes=ocr_notes,
                page_number=page_number,
                blocks=blocks,
                ocr_uncertain=ocr_uncertain,
                **kwargs,
            )
            result.timings.update(measured, pipeline=time.perf_counter() - started)
            for name, agent in agents.items():
                client = getattr(agent, "_client", None)
                attempts = getattr(client, "last_attempts", 0)
                usage = _agent_usage(agent) or {}
                if isinstance(attempts, int) and attempts:
                    usage["attempts"] = attempts
                    usage["retries"] = max(0, attempts - 1)
                if usage:
                    result.usage[name] = usage
            return result
        finally:
            page_timings.reset(token)

    async def _extract_page_impl(
        self,
        filename: str,
        page_text: str,
        image_bytes: bytes | None = None,
        image_media_type: str | None = None,
        ocr_notes: list[str] | None = None,
        page_number: int = 1,
        blocks: list | None = None,
        ocr_uncertain: bool = False,
        doc_type: str | None = None,
    ) -> ExtractionResult:
        trace = self.tracer.start_trace(
            "extract-document",
            input_data={
                "filename": filename,
                "text_chars": len(page_text or ""),
                "text_excerpt": (page_text or "")[:1000],
            },
            metadata={
                "has_image": bool(image_bytes),
                "router_model": self.settings.router_model_name,
                "extraction_model": self.settings.extraction_model_name,
                "judge_model": self.settings.judge_model_name,
                "prompt_versions": active_prompt_versions(),
                "prompt_version": compound_prompt_version(),
            },
        )

        # Track extraction source. All text now comes from the configured
        # local OCR engine, so the source is "ocr". `image_bytes`/
        # `image_media_type` are kept only for backward compatibility
        # (old callers/tests); when text is empty but image bytes are
        # provided we OCR them here instead of using a vision model.
        _ = image_media_type  # legacy kwarg, intentionally unused (text-only pipeline)
        has_text = bool(page_text and page_text.strip())
        extraction_source: str | None = None
        ocr_performed = False

        if not has_text and image_bytes:
            try:
                async with self.timeout_guard.track("ocr"):
                    ocr_pages = await self.ocr.aparse_file(image_bytes, filename)
                page_text = "\n\n".join(p for p in ocr_pages if p.strip())
                has_text = bool(page_text.strip())
                ocr_performed = bool(has_text)
                if ocr_performed:
                    logger.info("RapidOCR recovered text for %s (%d chars)", filename, len(page_text))
                    trace.span("ocr-page", output={"chars": len(page_text), "fallback": True})
            except Exception as exc:
                logger.warning("RapidOCR image fallback failed for %s: %s", filename, exc)

        # Apply content size limits
        if self.settings.guards_enabled and page_text:
            page_text = validate_document_text(
                page_text,
                self.content_limits,
                truncate=True,
            )

        # --- 0. Coherence gate (before ANY LLM call) ---
        # Fail fast on degenerate OCR text: a small model stalls ~2000s
        # (full retry budget) on script-salad scans instead of extracting.
        # An honest flagged error beats a timeout burn with no data.
        # The OCR stage assesses the same rule on the same text when it
        # records review reasons; this gate re-checks text-only input
        # (Temporal/direct callers) so unreadable pages never reach the LLM.
        if page_text and page_text.strip():
            coherent = is_ocr_text_coherent(page_text)
            if not coherent:
                errors = [
                    "OCR text incoherent: the page could not be transcribed reliably "
                    "(possible handwriting or degraded print) and no recovery produced "
                    "usable text. The original preview and recognized text are preserved "
                    "for review."
                ]
                errors.extend(f"OCR: {r}" for r in (ocr_notes or []) if r)
                trace.span("validate-fields", output={"error": "ocr text incoherent"}, level="ERROR")
                trace.end(level="ERROR")
                self.tracer.flush()
                return ExtractionResult(
                    doc_type="invoice",  # placeholder; no routing ran
                    fields=[],
                    validation_errors=errors,
                    needs_review=True,
                    completeness_score=0.0,
                    error=errors[0],
                    failed_stage="ocr",
                )

        # --- 1. Router (text-only; page_text comes from local OCR) ---
        # An explicit user-selected type bypasses classification (one fewer
        # LLM call) WITHOUT bypassing extraction validation downstream.
        router_gen = trace.generation(
            "classify-document",
            model=self.settings.router_model_name,
            input_data={"filename": filename, "text_excerpt": (page_text or "")[:1000]},
        )
        explicit_type = parse_doc_type(doc_type)
        if explicit_type is not None:
            from app.schemas.documents import RoutingDecision as _RoutingDecision

            routing = _RoutingDecision(
                doc_type=explicit_type, language=None, confidence=1.0,
                reason="explicit user selection (classification bypassed)",
            )
            router_gen.end(
                output={"doc_type": routing.doc_type, "confidence": 1.0,
                        "reason": routing.reason, "bypassed": True},
                usage=None,
            )
            logger.info("Router bypassed: explicit doc_type=%s", routing.doc_type)
        else:
            try:
                async with self.timeout_guard.track("router"):
                    routing = await self.router.classify(
                        filename=filename,
                        text_hint=sanitize_document_text(page_text) or None,
                    )
                router_gen.end(
                    output={"doc_type": routing.doc_type, "confidence": routing.confidence,
                            "reason": routing.reason},
                    usage=_agent_usage(self.router),
                )
            except Exception as exc:
                details = _provider_error_details(
                    exc,
                    stage="router",
                    model=self.settings.router_model_name,
                    provider=self.settings.llm_provider,
                )
                router_gen.end(
                    output={"error": str(exc), "provider": details.model_dump() if details else None},
                    level="ERROR",
                    status_message=str(exc)[:500],
                )
                trace.end(level="ERROR")
                self.tracer.flush()
                if details is not None:
                    logger.error(
                        "Router provider error model=%s status=%s code=%s request_id=%s message=%.2000s",
                        details.model, details.status, details.code,
                        details.request_id, details.message or "",
                    )
                return ExtractionResult(
                    doc_type="invoice",  # placeholder; overwritten below by schema-safe error path
                    fields=[],
                    validation_errors=[f"Router failed: {exc}"],
                    needs_review=True,
                    completeness_score=0.0,
                    error=f"Router failed: {exc}",
                    failed_stage="router",
                    error_details=details,
                )

        # --- 1b. Unsupported / low-confidence short-circuits (before ANY extractor) ---
        if routing.doc_type == "unsupported":
            from app.schemas.documents import UNSUPPORTED_DOCUMENT_MARKER

            message = (
                f"Unsupported document type: this document {UNSUPPORTED_DOCUMENT_MARKER} "
                f"(invoice, purchase_order, delivery_note). "
                f"[Router note: {routing.reason or 'no reason'}]"
            )
            # Note: router_gen already ended with the classification output
            # above — record the short-circuit as its own span (a second
            # end() on the generation double-ends it in the SDK).
            trace.span("router-short-circuit",
                       output={"doc_type": "unsupported",
                               "confidence": routing.confidence,
                               "reason": routing.reason})
            trace.end()
            self.tracer.flush()
            logger.info("Router short-circuit: unsupported (%s)", filename)
            return ExtractionResult(
                doc_type="invoice",  # placeholder; no supported type matched
                fields=[],
                validation_errors=[message],
                needs_review=True,
                completeness_score=0.0,
                error=message,
                failed_stage="router",
                language=routing.language,
                routing_reason=routing.reason,
            )
        from app.schemas.documents import ROUTER_LOW_CONFIDENCE_THRESHOLD

        if routing.confidence < ROUTER_LOW_CONFIDENCE_THRESHOLD:
            # Same single-end rule as above: router_gen already ended.
            trace.span("router-short-circuit",
                       output={"doc_type": routing.doc_type,
                               "confidence": routing.confidence,
                               "reason": routing.reason,
                               "short_circuited": "low_confidence"})
            logger.info(
                "Router short-circuit: low confidence %.2f (%s)",
                routing.confidence, filename,
            )
            low_conf_msg = (
                f"Router uncertain (confidence {routing.confidence:.2f} "
                f"< {ROUTER_LOW_CONFIDENCE_THRESHOLD:.2f}): extraction skipped "
                "instead of extracting on a guess. "
                f"[Router note: {routing.reason or 'no reason'}]"
            )
            if routing.doc_type in ("invoice", "purchase_order", "delivery_note"):
                low_conf_type = routing.doc_type
            else:  # defensive: never emit a non-registry type downstream
                low_conf_type = "invoice"
            trace.end()
            self.tracer.flush()
            return ExtractionResult(
                doc_type=low_conf_type,
                fields=[],
                validation_errors=[low_conf_msg],
                needs_review=True,
                completeness_score=0.0,
                language=routing.language,
                routing_reason=routing.reason,
            )

        # --- 1c. Language-tag correction (router noise, not doc content) ---
        # Tesseract with Thai traineddata emits Thai-looking fragments on
        # English print and a small router LLM obeys the noise. A Latin
        # majority page tagged "th" is corrected to "en" with the reason
        # annotated; type, confidence and the original reason are preserved.
        routing = _correct_router_language(routing, page_text)

        # Invariant: "unsupported" and sub-threshold guesses returned above,
        # so from here routing.doc_type is one of the 3 supported types —
        # every downstream registry/catalog lookup below is total.
        assert routing.doc_type in ("invoice", "purchase_order", "delivery_note")

        # --- 2. Extractor (per doc type) ---
        few_shot: list[dict] | None = None
        limit = self.settings.few_shot_examples_per_doc_type
        if limit > 0:
            few_shot = self.knowledge_base.get_few_shot_examples(routing.doc_type, limit=limit) or None

        extract_gen = trace.generation(
            "extract-fields",
            model=self.settings.extraction_model_name,
            input_data={"doc_type": routing.doc_type,
                        "catalog_fields": len(self.catalog.get_field_names(routing.doc_type)),
                        "text_chars": len(page_text or "")},
        )
        try:
            async with self.timeout_guard.track("extractor"):
                call = await self.extractors[routing.doc_type].extract_call(
                    text=page_text or "",
                    few_shot=few_shot,
                    page_number=page_number,
                )
                fields, tables_raw, new_field_names = call.fields, call.tables, call.new_field_names
            if not fields and not tables_raw:
                raise ValueError("No usable fields were extracted from the OCR text. "
                                 "Check the OCR text or retry extraction with a suitable text model.")
            extract_gen.end(
                output={"fields": [{"name": f.name, "confidence": f.confidence} for f in fields],
                        "new_fields": new_field_names,
                        "tables": len(tables_raw),
                        "page_number": page_number},
                usage=_agent_usage(self.extractors[routing.doc_type]),
            )
        except Exception as exc:
            details = _provider_error_details(
                exc,
                stage="extractor",
                model=self.settings.extraction_model_name,
                provider=self.settings.llm_provider,
            )
            extract_gen.end(
                output={"error": str(exc), "provider": details.model_dump() if details else None},
                level="ERROR",
                status_message=str(exc)[:500],
            )
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(routing, f"Extractor failed: {exc}", "extractor", error_details=details)

        # Determine extraction source. The unified pipeline always OCRs with
        # RapidOCR first, so successful extractions are "ocr". "text" is kept
        # for callers that inject raw text directly (unit tests).
        if fields and (ocr_performed or has_text):
            extraction_source = "ocr"
        elif has_text:
            extraction_source = "text"

        # Page isolation: extractor already returns deep-copied tables bound
        # to this page. Assign stable block IDs for evidence resolution.
        tables = list(tables_raw or [])
        try:
            from app.services.evidence import assign_block_ids

            if blocks:
                assign_block_ids(blocks, page_number)
        except Exception:
            pass
        ocr_uncertain = bool(ocr_uncertain or (ocr_notes or []))

        # --- 3. Validator (deterministic + hallucination guard) ---
        # Runs BEFORE the Judge. Strict evidence check applies
        # (is_image_extraction=False). Coverage counts accepted populated
        # required fields only.
        try:
            async with self.timeout_guard.track("validator"):
                (
                    validation_errors,
                    completeness,
                    needs_review,
                    accepted_fields,
                    accepted_tables,
                    rejected,
                    det_issues,
                ) = self.validator.validate_detailed(
                    doc_type=routing.doc_type,
                    fields=fields,
                    document_text=page_text or None,
                    is_image_extraction=False,
                    tables=tables,
                    blocks=blocks,
                    page_number=page_number,
                    ocr_uncertain=ocr_uncertain,
                )
            trace.span("validate-fields", output={"errors": validation_errors,
                                                   "completeness": completeness,
                                                   "needs_review": needs_review,
                                                   "rejected": len(rejected)})
        except Exception as exc:
            trace.span("validate-fields", output={"error": str(exc)}, level="ERROR")
            trace.end(level="ERROR")
            self.tracer.flush()
            return self._failed(
                routing, f"Validator failed: {exc}", "validator",
                fields=fields,
            )

        # Catalog registration on ACCEPTED fields only (evidence-checked).
        outcome = register_discovered_fields(self.catalog, routing.doc_type, accepted_fields, document_text=page_text)
        accepted_fields = outcome.fields
        if outcome.added:
            trace.span("catalog-update", output={"added": outcome.added})

        # --- 4. Judge (deterministic validation runs first) ---
        # Strengthened skip: accepted types/evidence, table structure/row/col
        # evidence, required coverage, OCR uncertainty, unresolved findings,
        # verbatim numeric spans — confidence necessary but never sufficient.
        from app.agents.judge import should_skip_judge as _should_skip

        judge_result = None
        try:
            _min_conf = min(
                [float(f.confidence) for f in accepted_fields if f.value is not None]
                + [float(c.confidence) for t in accepted_tables for row in t.rows for c in row]
                or [0.0]
            )
        except Exception:
            _min_conf = 0.0
        _rejected_blocking = any(
            True
            for _c in rejected
            # info placeholders/duplicates do not block the skip gate
            if not (
                "placeholder" in (_c.rejection_reason or "").lower()
                or "duplicate scalar/table" in (_c.rejection_reason or "").lower()
            )
        ) or any(i.severity in ("warning", "error") for i in det_issues if "placeholder" not in (i.explanation or "").lower())
        _skip_ok, _skip_reason = _should_skip(
            accepted_fields=accepted_fields,
            accepted_tables=accepted_tables,
            required_coverage=completeness,
            validation_errors=validation_errors,
            rejected_warning_or_error=_rejected_blocking,
            ocr_uncertain=ocr_uncertain,
            min_confidence=_min_conf,
            confidence_threshold=self.settings.judge_skip_confidence,
            numeric_verbatim=_all_numeric_spans_verbatim(accepted_fields, accepted_tables, page_text),
        )
        skip_judge = bool(self.settings.judge_skip_when_clean and _skip_ok)
        if skip_judge:
            trace.span("judge-skipped", output={"reason": _skip_reason})
            logger.info("Judge skipped for %s — %s", filename, _skip_reason)
        else:
            _skip_note = "" if self.settings.judge_skip_when_clean else "skip disabled; "
            judge_gen = trace.generation(
                "judge-extraction",
                model=self.settings.judge_model_name,
                input_data={"doc_type": routing.doc_type,
                            "field_count": len(accepted_fields),
                            "skip_gate": f"{_skip_note}{_skip_reason}"},
            )
            try:
                async with self.timeout_guard.track("judge"):
                    judge_result = await self.judge.evaluate(
                        fields=accepted_fields,
                        source_text=page_text or None,
                        tables=accepted_tables,
                        validation_findings=list(validation_errors),
                        ocr_uncertain=ocr_uncertain,
                    )
                judge_gen.end(
                    output={"score": judge_result.score,
                            "issues": [i.model_dump() if hasattr(i, "model_dump") else i
                                       for i in (judge_result.issues or [])],
                            "notes": judge_result.notes},
                    usage=_agent_usage(self.judge),
                )
                needs_review = needs_review or _judge_demands_review(judge_result)
            except Exception as exc:
                details = _provider_error_details(
                    exc,
                    stage="judge",
                    model=self.settings.judge_model_name,
                    provider=self.settings.llm_provider,
                )
                judge_gen.end(
                    output={"error": str(exc), "provider": details.model_dump() if details else None},
                    level="ERROR",
                    status_message=str(exc)[:500],
                )
                validation_errors.append(f"Judge unavailable: {exc}")
                needs_review = True
                judge_result = None

        # --- PII Detection (log but don't block; accepted data only) ---
        if self.settings.pii_detection_enabled:
            for field in accepted_fields:
                if field.value is not None and isinstance(field.value, str):
                    pii_matches = self.pii_detector.check_field_value(field.name, field.value)
                    for match in pii_matches:
                        self.audit_logger.log_pii_detected(
                            client_id=filename,
                            field_name=field.name,
                            pii_type=match.pii_type.value,
                            value_preview=field.value[:50],
                        )

        # --- Auto-eval vs ground truth (when KB has a matching file) ---
        auto_eval = None
        gt = self.knowledge_base.get_ground_truth(Path(filename).stem)
        if gt is not None:
            prediction = {f.name: f.value for f in accepted_fields}
            auto_eval = self.evaluate(prediction=prediction, ground_truth=gt, doc_type=routing.doc_type)
            trace.span("auto-eval", output={"f1": auto_eval.f1})
            trace.score("auto-eval-f1", float(auto_eval.f1),
                        comment="ground-truth F1 (KB match)")

        # Structured review: deterministic findings + Judge issues, deduped
        # without erasing distinct concerns. Rejected candidates stay separate
        # from accepted fields/tables (excluded from normal exports).
        from app.schemas.documents import StructuredReviewIssue

        review_issues: list[StructuredReviewIssue] = list(det_issues or [])
        for ji in (judge_result.issues if judge_result is not None else []) or []:
            review_issues.append(
                StructuredReviewIssue(
                    category=(getattr(ji, "category", None) or "unsupported"),
                    target=(getattr(ji, "target", None) or f"field:{ji.field}"),
                    severity=(getattr(ji, "severity", "warning") or "warning"),
                    evidence=getattr(ji, "evidence", None),
                    explanation=(getattr(ji, "explanation", None) or ji.message),
                )
            )
        # Deduplicate (category, target, severity, evidence, explanation).
        _seen_ri: set[tuple] = set()
        _deduped: list[StructuredReviewIssue] = []
        for ri in review_issues:
            key = (ri.category, ri.target, ri.severity, ri.evidence or "", ri.explanation)
            if key in _seen_ri:
                continue
            _seen_ri.add(key)
            _deduped.append(ri)
        review_issues = _deduped

        if accepted_fields or accepted_tables:
            acceptance_status = "accepted"
        elif rejected:
            acceptance_status = "unresolved"
        else:
            acceptance_status = "unevaluated"

        trace.set_io(
            input_data={"filename": filename, "page_number": page_number},
            output_data={
                "doc_type": routing.doc_type,
                "needs_review": needs_review,
                "accepted_fields": len(accepted_fields),
                "rejected": len(rejected),
                "fields": [
                    {"name": f.name, "value": f.value, "confidence": f.confidence}
                    for f in accepted_fields
                ],
            },
        )
        trace.update(metadata={"doc_type": routing.doc_type,
                               "extraction_source": extraction_source,
                               "field_count": len(accepted_fields),
                               "rejected_count": len(rejected),
                               "judge_skipped": skip_judge,
                               "acceptance_policy": "v1.0.0"})
        trace.score("completeness", float(completeness))
        trace.score("needs_review", 1.0 if needs_review else 0.0)
        if judge_result is not None:
            trace.score("judge-score", float(judge_result.score))

        trace.end()
        self.tracer.flush()

        # A skipped or unavailable Judge must never display as passed.
        if skip_judge:
            judge_status = "skipped"
        elif judge_result is None:
            judge_status = "unavailable"
        elif _judge_demands_review(judge_result):
            judge_status = "flagged"
        else:
            judge_status = "passed"
        return ExtractionResult(
            doc_type=routing.doc_type,
            language=routing.language,
            fields=accepted_fields,
            tables=accepted_tables,
            judge_status=judge_status,  # type: ignore[assignment]
            validation_errors=validation_errors,
            needs_review=needs_review,
            completeness_score=completeness,
            judge=judge_result,
            routing_reason=routing.reason,
            full_text=page_text or None,
            extraction_source=extraction_source,
            auto_evaluation=auto_eval,
            rejected_candidates=rejected,
            review_issues=review_issues,
            acceptance_status=acceptance_status,  # type: ignore[assignment]
            acceptance_policy_version="v1.0.0",
        )

    def _failed(
        self,
        routing,
        message: str,
        stage,
        fields: list[ExtractedField] | None = None,
        error_details: ProviderErrorDetails | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(
            doc_type=routing.doc_type,
            language=routing.language,
            routing_reason=routing.reason,
            fields=fields or [],
            validation_errors=[message],
            needs_review=True,
            completeness_score=0.0,
            error=message,
            failed_stage=stage,
            error_details=error_details,
        )

    # ------------------------------------------------------------------
    # Batch jobs
    # ------------------------------------------------------------------

    def create_batch(self) -> BatchCreateResponse:
        job = self.job_store.create()
        return BatchCreateResponse(job_id=job.job_id, status=job.status)

    def clear_history(self) -> dict[str, int]:
        """Delete all history jobs, related rows, stored sources, and cache.

        Raises ValueError while jobs are active (queued/processing) so
        background work cannot recreate rows mid-wipe. Ground truth,
        catalogs, models, settings, and backups live elsewhere and are
        never touched.
        """
        counts = self.job_store.clear_all_jobs()
        counts["sources"] = self.sources.clear()
        try:
            if self.result_cache is not None:
                counts["result_cache"] = self.result_cache.clear()
        except Exception:
            pass
        logger.info("History cleared: %s", counts)
        return counts

    def get_batch_status(self, job_id):
        job = self.job_store.get(job_id)
        if job is None:
            return None
        result = None
        # Completed and checkpointed partial pages remain readable, including
        # after a later page fails or the worker is interrupted.
        if job.result is not None and job.result.get("documents"):
            try:
                result = FileExtractionResponse.model_validate(job.result)
            except Exception:
                logger.warning("Stored result for job %s failed validation", job_id)
                result = None
        error = None
        if job.status == "failed":
            try:
                error = self.job_store.get_error(job_id)
            except Exception:
                error = None
        return BatchStatusResponse(job_id=job.job_id, status=job.status, result=result, error=error, progress=job.progress)

    async def run_job(
        self,
        job_id: UUID,
        parts: list[UploadedFilePart],
        force_refresh: bool = False,
        disable_caches: bool = False,
        doc_type: str | None = None,
    ) -> None:
        """Background-task entry point: run extraction for an existing job.

        Waits before OCR/stage timers. Failures are persisted; cancellation
        is persisted and re-raised so the background-task owner can clean up.
        """
        queued_at = time.perf_counter()
        logger.info("Job %s queued", job_id)
        token = job_context.set(str(job_id))
        queue_token = queue_wait.set(0)
        try:
            # Fast path BEFORE the processing-job lock: a repeated completed
            # upload must not wait behind a slow job or redo OCR/LLM work.
            # Full hits return here; anything else falls through to the
            # normal locked path (per-page cache hits still skip LLM rework
            # there). Touches no shared mutable extraction state.
            _fast_cache = getattr(self, "result_cache", None)
            if (
                _fast_cache is not None
                and _fast_cache.enabled_and_ready
                and not force_refresh
                and not disable_caches
            ):
                try:
                    fast = await self._try_fast_path(job_id, parts)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Fast-path lookup skipped for job %s: %s", job_id, exc)
                    fast = None
                if fast is not None:
                    queue_wait.set(0.0)
                    return
            async with self._job_lock:
                self.job_store.mark_processing(job_id)
                logger.info("Job %s processing", job_id)
                queue_wait.set(time.perf_counter() - queued_at)
                # No broad fallback here: an internal TypeError must fail
                # honestly (persisted below), never silently duplicate the
                # whole job with a second extraction run.
                await self.extract_group(
                    parts,
                    job_id=job_id,
                    force_refresh=force_refresh,
                    disable_caches=disable_caches,
                    doc_type=doc_type,
                )
                job = self.job_store.get(job_id)
                logger.info("Job %s %s", job_id, job.status if job else "unknown")
        except asyncio.CancelledError:
            self.job_store.fail_job(job_id, "Cancelled (server shutdown)")
            logger.info("Job %s cancelled", job_id)
            raise
        except Exception as exc:
            self.job_store.fail_job(job_id, str(exc))
            logger.error(
                "Background extraction %s failed: %s: %.500s",
                job_id, type(exc).__name__, exc,
            )
        finally:
            job_context.reset(token)
            queue_wait.reset(queue_token)

    async def _try_fast_path(
        self,
        job_id: UUID,
        parts: list[UploadedFilePart],
    ):
        """Serve a fully-cached repeat upload without the lock, OCR, or LLM.

        Returns the completed FileExtractionResponse on a FULL hit (job row
        marked completed), else None. Any miss/corruption/ambiguity falls
        through to the normal path — never a partial result presented as
        complete. Creates a normal new submission: fresh IDs, saved sources,
        rendered previews, current download references. Original computation
        timestamps stay in cache_metadata; current latency is reported
        separately in timings.
        """
        from uuid import uuid4

        from app.schemas.documents import ResultCacheMetadata

        cache = getattr(self, "result_cache", None)
        if cache is None or not cache.enabled_and_ready:
            return None
        started = time.perf_counter()
        # 1. Manifest lookup per file (file identity + page count + FULL
        #    config fingerprint — no invalidation is weakened to make this
        #    easier; a config/model/prompt/catalog change is a miss).
        planned: list[tuple[UploadedFilePart, int, dict[int, str], dict[int, str]]] = []
        for part in parts:
            page_count = _count_pages(part.raw_content, part.filename)
            if not page_count:
                return None
            key = cache.manifest_key(
                file_bytes=part.raw_content, filename=part.filename, page_count=page_count,
            )
            pages, texts, _meta = cache.manifest_get(key)
            if not pages or sorted(pages) != list(range(1, page_count + 1)):
                return None
            planned.append((part, page_count, pages, texts))
        # 2. Resolve every full page fingerprint (corrupt/expired/evicted
        #    entries are misses that fall through to the normal path).
        resolved: list[tuple[UploadedFilePart, int, int, str, str | None, object]] = []
        lookup_ms = 0.0
        for part, page_count, pages, texts in planned:
            for page_number in range(1, page_count + 1):
                fingerprint = pages[page_number]
                _t0 = time.perf_counter()
                try:
                    hit, meta = cache.get(fingerprint)
                finally:
                    lookup_ms += (time.perf_counter() - _t0) * 1000
                if hit is None:
                    return None
                resolved.append((part, page_count, page_number, fingerprint,
                                 texts.get(page_number), (hit, meta)))
        # 3. Full hit: mark processing (so History-clear refuses mid-serve),
        #    save sources, render previews (no OCR/models), remap references.
        self.job_store.mark_processing(job_id)
        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        request_meta = FileUploadMeta(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )
        job_id_str = str(job_id)
        documents = []
        render_ms = 0.0
        for part, page_count, page_number, fingerprint, page_text, (hit, meta) in resolved:
            documents.append((part, page_count, page_number, fingerprint, page_text, hit, meta))
        # Save each source once, render its previews once.
        source_ids: dict[int, object] = {}
        rendered: dict[int, list[bytes]] = {}
        for part, page_count, _page_number, _fp, _pt, _hit, _meta in documents:
            if id(part) in source_ids:
                continue
            source_ids[id(part)] = self.sources.save(part.filename, part.raw_content, part.content_type)
            _t0 = time.perf_counter()
            previews = _render_previews(part.raw_content, part.filename, self.settings.ocr_dpi)
            render_ms += (time.perf_counter() - _t0) * 1000
            if not previews or len(previews) != page_count:
                # No previews, no fast path: previews/download references are
                # part of a normal submission — fall through instead of
                # serving a degraded result.
                return None
            rendered[id(part)] = previews
        final_docs = []
        for part, page_count, page_number, fingerprint, page_text, hit, meta in documents:
            source = self.sources.reference(
                source_ids[id(part)], page_number, page_count, rendered[id(part)][page_number - 1],
            )
            doc = hit.model_copy(update={
                "id": uuid4(),
                "source": source,
                "ocr_blocks": [],
                "full_text": page_text,
                "timings": {},
                "usage": {},
                "cache_metadata": ResultCacheMetadata(
                    fingerprint=fingerprint,
                    computed_at=(meta or {}).get("computed_at_iso"),
                    original_timings=dict((meta or {}).get("original_timings", {}) or {}),
                    acceptance_policy_version=(meta or {}).get("acceptance_policy", "v1.0.0"),
                    cache_lookup_ms=lookup_ms,
                    hit_type="full",
                ),
            })
            final_docs.append(doc)
        elapsed = time.perf_counter() - started
        response = FileExtractionResponse(
            request=request_meta, documents=final_docs, job_id=job_id_str,
            file_errors=[],
            timings={"processing": elapsed, "queue": 0.0,
                     "result_cache_hits": float(len(final_docs)),
                     "result_cache_misses": 0.0,
                     "result_cache_lookup_ms": lookup_ms,
                     "result_cache_render_ms": render_ms,
                     "fast_path": 1.0},
        )
        progress = {"completed_pages": len(final_docs), "total_pages": len(final_docs), "stage": "completed"}
        self.job_store.set_progress(job_id, progress)
        self.job_store.save_result(job_id, response.model_dump(mode="json"))
        logger.info(
            "Result cache fast-path full hit: job=%s pages=%d elapsed=%.2fs lookup_ms=%.1f render_ms=%.1f",
            job_id, len(final_docs), elapsed, lookup_ms, render_ms,
        )
        return response

