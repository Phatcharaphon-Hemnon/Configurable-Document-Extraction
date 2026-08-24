"""API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import get_settings
from app.schemas.documents import (
    BatchCreateResponse,
    BatchStatusResponse,
    EvaluateRequest,
    EvaluateResponse,
    FileExtractionResponse,
)
from app.services.extraction_service import DocumentExtractionService, UploadedFilePart

router = APIRouter()
settings = get_settings()
service = DocumentExtractionService(settings=settings)


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


@router.post("/extract", response_model=FileExtractionResponse)
async def extract_document(files: list[UploadFile] = File(...)) -> FileExtractionResponse:
    """Upload one or more files (images or PDFs).

    - Multiple files selected together are treated as pages of one logical upload.
    - A multi-page PDF yields one ExtractionResult per page (multi-document support).
    - Images go straight to the vision model (OCR + ICR); PDFs go through LlamaParse.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    parts: list[UploadedFilePart] = []
    for f in files:
        contents = await f.read()
        parts.append(
            UploadedFilePart(
                filename=f.filename or "uploaded-document",
                content_type=f.content_type,
                raw_content=contents,
            )
        )

    try:
        return await service.extract_group(parts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
