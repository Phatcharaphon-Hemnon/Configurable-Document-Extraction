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
        "frontend_origins": settings.frontend_origin_list,
        "schema_mode": settings.schema_mode,  # reflect the active mode
        "recommended_extraction_model": {
            "name": settings.recommended_extraction_model_name,
            "display_name": settings.recommended_extraction_model_display_name,
            "reason": settings.recommended_extraction_model_reason,
        },
        "endpoints": ["/extract", "/templates", "/extract/batch", "/jobs/{job_id}", "/evaluate"],
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/extract", response_model=FileExtractionResponse)
async def extract_document(files: list[UploadFile] = File(...)) -> FileExtractionResponse:
    """Accepts one or more files selected together in a single upload action.

    - Multiple files here are treated as PAGES of ONE logical document
      (e.g. page1.jpg + page2.jpg of the same invoice).
    - A single PDF file is automatically split into one page per PDF page.
    - The response contains one ExtractionResult per detected page.
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
    templates = service.list_templates()
    if settings.schema_mode == "strict":
        base = service.knowledge_base.base_path / "field_catalog"
        if not base.exists() or not list(base.glob("*.json")):
            templates = []
    return {
        "schema_mode": settings.schema_mode,
        "templates": templates,
    }


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
# Temporal PoC endpoint — test-only, does NOT replace /extract
# ---------------------------------------------------------------------------

@router.post("/extract/temporal-poc")
async def extract_temporal_poc(files: list[UploadFile] = File(...)) -> dict:
    """**PoC-only** endpoint exercising the Temporal classify workflow.

    Limitations vs. ``/extract``:

    * Only processes ``files[0]`` — multi-page grouping is not wired.
    * Skips ``LlamaParseClient`` entirely — raw bytes are decoded as
      UTF-8 and passed straight to the workflow as ``text_hint``.
    * No image path — vision classification is deferred.
    """
    from uuid import uuid4

    from app.temporal.client import TASK_QUEUE, get_temporal_client
    from app.temporal.workflows import ClassifyDocumentWorkflow

    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        # PoC scope cut: skip LlamaParse / image branching — just treat
        # raw bytes as plain text.
        raw_content = await files[0].read()
        text_hint = raw_content.decode("utf-8", errors="ignore")
        filename = files[0].filename or "uploaded-document"

        client = await get_temporal_client()
        handle = await client.start_workflow(
            ClassifyDocumentWorkflow.run,
            args=[filename, text_hint],
            id=f"classify-poc-{uuid4()}",
            task_queue=TASK_QUEUE,
        )
        result = await handle.result()
        return result  # type: ignore[return-value]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Temporal worker unavailable or workflow failed: {exc}",
        ) from exc
