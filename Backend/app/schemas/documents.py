from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# OPEN SCHEMA NOTE
# --------------------------------------------------------------------------
# doc_type is now a free-form string proposed by the Router agent (e.g.
# "invoice", "receipt", "medical_form", "id_card") instead of a fixed Enum.
# We no longer maintain a hardcoded list of supported document types or a
# hardcoded field list per type. The Router proposes suggested fields per
# document; the Extractor fills what it can find and may report additional
# fields beyond the suggestion. Validation is soft: only a document with
# literally no usable fields, or a field explicitly marked
# "likely_required" by the Router that is genuinely missing, affects
# needs_review — nothing hard-fails the whole document just because an
# optional field wasn't found.
# --------------------------------------------------------------------------


class DocumentLanguage(str, Enum):
    EN = "en"
    TH = "th"
    OTHER = "other"


class FieldDefinition(BaseModel):
    """A field the Router/Extractor expects or found — open, not tied to a fixed catalog.

    type/validation_rule are optional metadata carried over from any
    instructor-provided field catalog JSON on disk (see KnowledgeBaseRepository);
    they are informational only and not enforced by the open-schema pipeline.
    """

    name: str
    description: str | None = None
    likely_required: bool = False
    type: str | None = None
    validation_rule: str | None = None


class TemplateSchema(BaseModel):
    """Open, informational template entry — no longer tied to a fixed
    DocumentType enum. doc_type is whatever label the catalog file (or the
    fallback) uses; there is no restriction on what values are valid."""

    doc_type: str
    description: str
    language_support: list[DocumentLanguage] = Field(default_factory=list)
    fields: list[FieldDefinition] = Field(default_factory=list)


class DetectedDocumentRegion(BaseModel):
    """Describes where a single document was found within an uploaded file that
    may contain multiple distinct documents (e.g. an invoice and a PO scanned
    together in one image)."""

    index: int
    doc_type_guess: str
    position_description: str = Field(
        description="Human-readable description of where this document sits in the "
        "image, e.g. 'top half', 'left column', 'entire page' for single-document files."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class FileUploadMeta(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None


class ExtractionField(BaseModel):
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = None
    likely_required: bool = False


class ValidationIssue(BaseModel):
    field: str
    message: str
    severity: str = "error"


class ValidationResult(BaseModel):
    is_valid: bool
    completeness_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of suggested fields that were actually found. "
        "Informational — does not by itself block the document.",
    )
    issues: list[ValidationIssue] = Field(default_factory=list)


class JudgeIssue(BaseModel):
    field: str
    message: str
    severity: str = "warning"


class JudgeResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssue] = Field(default_factory=list)
    notes: str


class ExtractionResult(BaseModel):
    """Result for ONE detected document. A single uploaded file may produce
    multiple ExtractionResult entries if it contains multiple documents
    (see FileExtractionResponse)."""

    id: UUID = Field(default_factory=uuid4)
    doc_type: str | None = None
    language: DocumentLanguage | None = None
    routing_reason: str | None = None
    region: DetectedDocumentRegion | None = None
    suggested_fields: list[FieldDefinition] = Field(default_factory=list)
    extracted_fields: dict[str, ExtractionField] = Field(default_factory=dict)
    additional_fields: dict[str, ExtractionField] = Field(
        default_factory=dict,
        description="Fields found on the document that were not in suggested_fields.",
    )
    full_text: str | None = None
    validation: ValidationResult | None = None
    judge: JudgeResult | None = None
    needs_review: bool = False
    error: str | None = None
    failed_stage: str | None = Field(
        default=None,
        description="The pipeline stage (router, extractor, validator, judge) that failed, or None on success."
    )
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    auto_evaluation: "EvaluateResponse | None" = Field(
        default=None,
        description=(
            "Populated automatically when the uploaded filename matches a ground-truth "
            "file in the knowledge base (e.g. invoice_01.pdf → invoice_01.json). "
            "None for all other uploads — this is expected and not an error."
        ),
    )


class FileExtractionResponse(BaseModel):
    """Top-level response for one uploaded file. Contains one or more
    ExtractionResult entries — more than one if the file contained multiple
    distinct documents."""

    request: FileUploadMeta
    documents: list[ExtractionResult] = Field(default_factory=list)
    error: str | None = None


class BatchCreateResponse(BaseModel):
    job_id: UUID
    status: str = "queued"


class BatchStatusResponse(BaseModel):
    job_id: UUID
    status: str
    result: FileExtractionResponse | None = None


class EvaluateRequest(BaseModel):
    doc_type: str
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


# Resolve the forward reference used in ExtractionResult.auto_evaluation
ExtractionResult.model_rebuild()