from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import get_settings
from app.schemas.documents import BatchCreateResponse, BatchStatusResponse, EvaluateRequest, EvaluateResponse, ExtractionResult
from app.services.extraction_service import DocumentExtractionService

router = APIRouter()
settings = get_settings()
service = DocumentExtractionService(settings=settings)


@router.get("/")
def home() -> dict[str, object]:
    return {
        "name": settings.app_name,
        "status": "ok",
        "frontend_origins": settings.frontend_origin_list,
        "supported_doc_types": settings.supported_doc_type_list,
        "endpoints": ["/extract", "/templates", "/extract/batch", "/jobs/{job_id}", "/evaluate"],
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/extract", response_model=ExtractionResult)
async def extract_document(file: UploadFile = File(...)) -> ExtractionResult:
    contents = await file.read()
    text = contents.decode("utf-8", errors="ignore")
    try:
        return service.extract(filename=file.filename or "uploaded-document", content_type=file.content_type, text=text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/templates")
def list_templates() -> dict[str, object]:
    return {
        "supported_doc_types": settings.supported_doc_type_list,
        "templates": service.list_templates(),
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
    )
