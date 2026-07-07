from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    INVOICE = "invoice"
    PURCHASE_ORDER = "po"
    DELIVERY_NOTE = "delivery_note"


class DocumentLanguage(str, Enum):
    EN = "en"
    TH = "th"


class FieldDefinition(BaseModel):
    name: str
    type: str
    required: bool = True
    validation_rule: str


class TemplateSchema(BaseModel):
    doc_type: DocumentType
    description: str
    language_support: list[DocumentLanguage]
    fields: list[FieldDefinition]


class FileUploadMeta(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None


class ExtractionField(BaseModel):
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = None


class BaseExtractionResult(BaseModel):
    doc_type: DocumentType
    language: DocumentLanguage
    file: FileUploadMeta
    fields: dict[str, ExtractionField]
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationIssue(BaseModel):
    field: str
    message: str
    severity: str = "error"


class ValidationResult(BaseModel):
    is_valid: bool
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
    id: UUID = Field(default_factory=uuid4)
    request: FileUploadMeta
    doc_type: DocumentType | None = None
    language: DocumentLanguage | None = None
    routing_reason: str | None = None
    extracted_fields: dict[str, ExtractionField] = Field(default_factory=dict)
    full_text: str | None = None
    validation: ValidationResult | None = None
    judge: JudgeResult | None = None
    needs_review: bool = False
    error: str | None = None


class BatchCreateResponse(BaseModel):
    job_id: UUID
    status: str = "queued"


class BatchStatusResponse(BaseModel):
    job_id: UUID
    status: str
    result: ExtractionResult | None = None


class EvaluateRequest(BaseModel):
    doc_type: DocumentType
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
