"""API endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from app.core.config import get_settings
from app.guards.audit_logger import AuditLogger
from app.guards.input_guard import InputValidationError, validate_file_upload
from app.guards.output_guard import sanitize_error_message
from app.guards.rate_limiter import RateLimitConfig, RateLimiter
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateRequest,
    EvaluateResponse,
)
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
service = DocumentExtractionService(settings=settings)

# Initialize guards
rate_limiter = RateLimiter(
    RateLimitConfig(
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
        max_concurrent=settings.rate_limit_max_concurrent,
    )
)
audit_logger = AuditLogger(
    log_file=settings.audit_log_file if settings.audit_log_enabled else None
)


def _get_client_id(request: Request) -> str:
    """Extract client identifier from request."""
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Async extraction jobs
#
# POST /extract returns 202 immediately with a job_id; the pipeline runs in
# a background task and the client polls GET /jobs/{job_id}. Long-lived
# upload connections (refresh / retry / proxy drops) can therefore never
# kill an extraction — and a retry for the same bytes reuses the running job
# (single-flight) instead of stacking a duplicate pipeline.
# ---------------------------------------------------------------------------

_background_tasks: dict[UUID, asyncio.Task] = {}
_content_to_job: dict[str, UUID] = {}


def _content_fingerprint(parts: list[UploadedFilePart]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.filename.encode("utf-8", "ignore"))
        digest.update(part.raw_content)
    return digest.hexdigest()


@router.get("/")
def home() -> dict[str, object]:
    return {
        "name": settings.app_name,
        "status": "ok",
        "doc_types": ["invoice", "purchase_order", "delivery_note"],
        "frontend_origins": settings.frontend_origin_list,
        "temporal_enabled": settings.temporal_enabled,
        "extraction_model": settings.extraction_model_name,
        "langfuse_enabled": settings.langfuse_enabled,
        "endpoints": ["/extract", "/templates", "/extract/batch", "/jobs/{job_id}", "/evaluate"],
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/extract", response_model=BatchCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def extract_document(
    request: Request,
    files: list[UploadFile] = File(...),
) -> BatchCreateResponse:
    """Upload one or more files (images or PDFs) for async extraction.

    - Returns 202 immediately with `{job_id, status: "queued"}`.
    - Poll `GET /jobs/{job_id}` until `status` is `completed`/`failed`.
    - Multiple files selected together are treated as pages of one logical upload.
    - A multi-page PDF yields one ExtractionResult per page (multi-document support).
    - All files are OCR'd locally with RapidOCR, then extracted with the
      single text model (no vision model or cloud OCR required).
    - Re-uploading identical bytes while the job runs returns the SAME
      job_id instead of starting a duplicate pipeline (single-flight).
    """
    client_id = _get_client_id(request)

    # Check rate limit
    if settings.guards_enabled:
        allowed, reason = rate_limiter.check_rate_limit(client_id)
        if not allowed:
            audit_logger.log_rate_limit_exceeded(
                client_id,
                settings.rate_limit_max_requests,
                settings.rate_limit_window_seconds,
            )
            raise HTTPException(status_code=429, detail=reason)
        rate_limiter.record_request(client_id)

    scheduled = False
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        parts: list[UploadedFilePart] = []
        # Validate each file
        for f in files:
            contents = await f.read()
            if settings.guards_enabled:
                try:
                    await validate_file_upload(f, content=contents)
                except InputValidationError as exc:
                    audit_logger.log_file_validation_failed(
                        client_id,
                        f.filename or "unknown",
                        exc.code,
                        str(exc),
                    )
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

            parts.append(
                UploadedFilePart(
                    filename=f.filename or "uploaded-document",
                    content_type=f.content_type,
                    raw_content=contents,
                )
            )

        # Single-flight: identical bytes already being processed → reuse it.
        fingerprint = _content_fingerprint(parts)
        existing_job_id = _content_to_job.get(fingerprint)
        if existing_job_id is not None:
            existing_task = _background_tasks.get(existing_job_id)
            if existing_task is not None and not existing_task.done():
                logger.info("Single-flight hit: reusing running job %s", existing_job_id)
                return BatchCreateResponse(job_id=existing_job_id, status="queued")
            _content_to_job.pop(fingerprint, None)
            _background_tasks.pop(existing_job_id, None)

        total_size = sum(len(p.raw_content) for p in parts)
        combined_name = parts[0].filename if len(parts) == 1 else f"{len(parts)} files ({parts[0].filename}, ...)"
        job = service.job_store.create(
            filename=combined_name,
            content_type=parts[0].content_type if len(parts) == 1 else None,
            size_bytes=total_size,
        )

        def _done(task: asyncio.Task, _job_id: UUID = job.job_id, _fp: str = fingerprint) -> None:
            _background_tasks.pop(_job_id, None)
            if _content_to_job.get(_fp) == _job_id:
                _content_to_job.pop(_fp, None)
            if settings.guards_enabled:
                rate_limiter.record_completion(client_id)
            if task.cancelled():
                logger.warning("Background extraction %s was cancelled", _job_id)
            elif task.exception() is not None:
                logger.error("Background extraction %s raised: %s", _job_id, task.exception())

        task = asyncio.create_task(service.run_job(job.job_id, parts))
        task.add_done_callback(_done)
        _background_tasks[job.job_id] = task
        _content_to_job[fingerprint] = job.job_id
        scheduled = True
        return BatchCreateResponse(job_id=job.job_id, status="queued")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Sanitize error messages to prevent information leakage
        sanitized = sanitize_error_message(exc, include_details=settings.app_debug)
        logger.error("Extraction accept failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=sanitized) from exc
    finally:
        # The rate-limit slot is held until the background task finishes
        # (released in its done-callback); release here only when nothing
        # was scheduled.
        if settings.guards_enabled and not scheduled:
            rate_limiter.record_completion(client_id)


@router.get("/templates")
def list_templates() -> dict[str, object]:
    return {"templates": service.list_templates()}


@router.post("/extract/batch", response_model=BatchCreateResponse)
def create_batch() -> BatchCreateResponse:
    return service.create_batch()


@router.get("/jobs/{job_id}", response_model=BatchStatusResponse)
def get_job(job_id: str) -> BatchStatusResponse:
    from uuid import UUID

    try:
        parsed_job_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc

    result = service.get_batch_status(parsed_job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate(request: EvaluateRequest) -> EvaluateResponse:
    return service.evaluate(
        prediction=request.prediction,
        ground_truth=request.ground_truth,
        source_text=request.source_text,
        doc_type=request.doc_type,
    )


# ---------------------------------------------------------------------------
# Job History Endpoints
# ---------------------------------------------------------------------------


@router.get("/history")
def list_jobs(
    status: str | None = None,
    doc_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """List extraction jobs with filtering and pagination."""
    if not settings.database_enabled:
        raise HTTPException(
            status_code=400,
            detail="Job history requires database (DATABASE_ENABLED=true)",
        )

    jobs, total = service.job_store.list_jobs(
        status=status,
        limit=limit,
        offset=offset,
    )

    return {
        "jobs": jobs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/history/stats")
def get_stats() -> dict[str, object]:
    """Get extraction statistics."""
    if not settings.database_enabled:
        raise HTTPException(
            status_code=400,
            detail="Statistics require database (DATABASE_ENABLED=true)",
        )

    return service.job_store.get_stats()


@router.get("/history/{job_id}")
def get_job_history(job_id: str) -> dict:
    """Get a specific job from history."""
    if not settings.database_enabled:
        raise HTTPException(
            status_code=400,
            detail="Job history requires database (DATABASE_ENABLED=true)",
        )

    from uuid import UUID

    try:
        parsed_job_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc

    job = service.job_store.repo.get_job(parsed_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job


@router.delete("/history/{job_id}")
def delete_job(job_id: str) -> dict[str, str]:
    """Delete a job from history."""
    if not settings.database_enabled:
        raise HTTPException(
            status_code=400,
            detail="Job history requires database (DATABASE_ENABLED=true)",
        )

    from uuid import UUID

    try:
        parsed_job_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid job id") from exc

    deleted = service.job_store.repo.delete_job(parsed_job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")

    return {"message": "Job deleted successfully"}
