"""Pydantic contracts for the document extraction pipeline.

Fixed 3-document-type schema per project spec:
invoice | purchase_order | delivery_note.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

DocType = Literal["invoice", "purchase_order", "delivery_note"]

DOC_TYPES: tuple[DocType, ...] = ("invoice", "purchase_order", "delivery_note")


class DocumentLanguage(str):
    """ISO-ish language tag detected by the router ('en', 'th', 'other', ...)."""


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class FieldDefinition(BaseModel):
    """One field in the field catalog for a document type."""

    name: str
    description: str | None = None
    type: str | None = None  # "string" | "number" | "date" | "array" | ...
    required: bool = False
    source: str = "catalog"  # "catalog" | "ai_discovered"


class CatalogReconcileReport(BaseModel):
    """Result of reconciling AI-discovered field names against the catalog."""

    matched: list[str] = Field(default_factory=list)
    new_fields: list[str] = Field(default_factory=list)
    catalog_updated: bool = False


# ---------------------------------------------------------------------------
# Extraction results
# ---------------------------------------------------------------------------

class ExtractedField(BaseModel):
    """A single extracted key/value pair with provenance."""

    name: str
    value: str | float | date | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = Field(
        default=None,
        description="Quoted evidence from the document backing this value.",
    )
    is_new_field: bool = Field(
        default=False,
        description="True when the name was not in the field catalog and has been added to it.",
    )


class ValidationResult(BaseModel):
    is_valid: bool
    completeness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)


class JudgeIssue(BaseModel):
    field: str
    message: str
    severity: str = "warning"


class JudgeResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssue] = Field(default_factory=list)
    notes: str = ""


class RoutingDecision(BaseModel):
    doc_type: DocType
    language: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str | None = None


class ProviderErrorDetails(BaseModel):
    """Redacted provider failure for UI <details> + logs/Langfuse.

    Never carries raw bodies, headers, or keys — only status/code/message
    plus routing context (stage/model/provider) to identify the cause.
    """

    stage: str | None = None
    provider: str | None = None
    model: str | None = None
    error_type: str | None = None
    status: int | None = None
    code: str | None = None
    param: str | None = None
    type: str | None = None
    message: str | None = None
    request_id: str | None = None


class ExtractionResult(BaseModel):
    """Result for ONE document. A single upload may yield several results
    (multi-page / multi-document files) — see FileExtractionResponse."""

    id: UUID = Field(default_factory=uuid4)
    doc_type: DocType
    language: str | None = None
    fields: list[ExtractedField] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    needs_review: bool = False
    completeness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    judge: JudgeResult | None = None
    routing_reason: str | None = None
    full_text: str | None = None
    error: str | None = None
    failed_stage: Literal["router", "extractor", "validator", "judge"] | None = None
    error_details: ProviderErrorDetails | None = Field(
        default=None,
        description="Redacted provider failure (status/code/message/request_id) for UI details.",
    )
    extraction_source: Literal["vision", "ocr", "text"] | None = Field(
        default=None,
        description="How the document was processed: vision (direct image), ocr (OCR fallback), text (PDF text).",
    )
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    auto_evaluation: EvaluateResponse | None = None  # noqa: F821 (forward ref resolved below)


class FileUploadMeta(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None


class FileExtractionResponse(BaseModel):
    request: FileUploadMeta
    documents: list[ExtractionResult] = Field(default_factory=list)
    error: str | None = None
    job_id: str | None = None


# ---------------------------------------------------------------------------
# Templates / catalog API
# ---------------------------------------------------------------------------

class TemplateSchema(BaseModel):
    doc_type: DocType
    description: str = ""
    fields: list[FieldDefinition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    doc_type: DocType | None = None
    prediction: dict[str, Any]
    ground_truth: dict[str, Any]
    source_text: str | None = None


class EvaluateResponse(BaseModel):
    score: float
    precision: float
    recall: float
    f1: float
    summary: str
    mismatches: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Batch jobs
# ---------------------------------------------------------------------------

class BatchCreateResponse(BaseModel):
    job_id: UUID
    status: str = "queued"


class BatchStatusResponse(BaseModel):
    job_id: UUID
    status: str
    result: FileExtractionResponse | None = None
    error: str | None = None


ExtractionResult.model_rebuild()
