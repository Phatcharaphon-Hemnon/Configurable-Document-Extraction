"""Pydantic contracts for the document extraction pipeline.

Fixed 3-document-type schema per project spec:
invoice | purchase_order | delivery_note.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.prompts.registry import compound_prompt_version
from app.schemas.ocr import OCRBlock

DocType = Literal["invoice", "purchase_order", "delivery_note"]

DOC_TYPES: tuple[DocType, ...] = ("invoice", "purchase_order", "delivery_note")

# Router-only document type: the 3 fixed types plus "unsupported" for pages
# matching none of them (spec tables, grade charts, reference sheets, blank
# forms). Router routing contracts use RouterDocType; output contracts
# (ExtractionResult, ExtractionCallResult, extractor registry, DOC_TYPES)
# keep the strict 3-type DocType Literal — the extraction invariant is
# untouched, "unsupported" short-circuits before any extractor runs.
RouterDocType = Literal["invoice", "purchase_order", "delivery_note", "unsupported"]

# Marker substring shared by the API and the frontend for unsupported pages.
# Kept stable: web/src/utils/pipeline.ts keys off it.
UNSUPPORTED_DOCUMENT_MARKER = "does not match any supported type"

# Below this router confidence the page keeps its routed (supported) type
# but skips extraction honestly instead of extracting on a guess.
ROUTER_LOW_CONFIDENCE_THRESHOLD = 0.5


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
    # Thai display metadata (opt-in, never used for matching).
    # label_th: short Thai label; description_th: short Thai description.
    # Both are display-only — exact-name matching uses `name` only.
    label_th: str | None = None
    description_th: str | None = None


# ---------------------------------------------------------------------------
# Extraction results
# ---------------------------------------------------------------------------

class ExtractedField(BaseModel):
    """A single extracted key/value pair with provenance."""

    name: str
    value: str | float | date | None = None
    # No silent 1.0 default: callers must supply the model's estimate.
    # Missing confidence is 0.0 and forces review downstream.
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str | None = Field(
        default=None,
        description="Quoted evidence from the document backing this value.",
    )
    is_new_field: bool = Field(
        default=False,
        description="True when the name was not in the field catalog and has been added to it.",
    )
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    acceptance: AcceptanceStatus = "unevaluated"


class RegistrationOutcome(BaseModel):
    fields: list[ExtractedField]
    added: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)


class JudgeIssue(BaseModel):
    field: str
    message: str
    severity: str = "warning"
    # Structured-issue extensions (backward-compatible defaults).
    # category: mechanical | unsupported | row_column | type | semantic | ocr_ambiguity
    category: str | None = None
    # Stable target identifier, e.g. "field:invoice_date" or "cell:line_items/0/unit_price".
    target: str | None = None
    # Quoted supporting evidence (page-local source_span or finding text).
    evidence: str | None = None
    # Why the claim holds (reconciliation reason preserved for debugging).
    explanation: str | None = None


class JudgeResult(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssue] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Typed extraction calls, evidence, acceptance, review (page-isolated)
# ---------------------------------------------------------------------------

# Acceptance-policy version. Bumped whenever acceptance rules change; part of
# the result-cache fingerprint so policy edits invalidate stale results.
# v1.1.0 (2026-09-18): invoice letterhead supplier fallback + placeholder
# cells no longer withhold rows (column drop when unpopulated) — see
# docs/reports/seller_header_tax_placeholder_2026-09-18.md.
ACCEPTANCE_POLICY_VERSION = "v1.1.0"

AcceptanceStatus = Literal["accepted", "rejected", "unresolved", "unevaluated"]


class EvidenceReference(BaseModel):
    """Page-local source reference backing one extracted value.

    Resolved in backend code from OCR blocks/spans — never trusted from
    model-generated coordinates, IDs, or offsets. `block_id` is the stable
    per-page OCR block identifier (`page-{n}-block-{i}`); `subspan` is the
    verified contiguous quote inside the block; `box` is the block's stored
    bounding box (original OCR geometry, not model output).
    """

    block_id: str | None = None
    page_number: int = Field(ge=1, default=1)
    subspan: str | None = None
    box: tuple[float, float, float, float] | None = None
    engine: str | None = None
    # Role of this reference: "label" | "value" | "context". Label evidence
    # (the printed caption) stays distinguishable from value evidence.
    role: Literal["label", "value", "context"] | None = None


class RejectedCandidate(BaseModel):
    """A proposed value excluded from accepted data but kept for review."""

    candidate_id: str
    kind: Literal["field", "cell", "row", "table"] = "field"
    # Field name or cell location, e.g. "invoice_date" or "line_items/0/unit_price".
    location: str
    proposed_value: str | float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_evidence: str | None = None
    source_refs: list[EvidenceReference] = Field(default_factory=list)
    rejection_reason: str = ""
    validation_findings: list[str] = Field(default_factory=list)


class StructuredReviewIssue(BaseModel):
    """Deterministic/Judge review finding with a stable target."""

    category: Literal[
        "mechanical", "unsupported", "row_column", "type", "semantic", "ocr_ambiguity"
    ] = "unsupported"
    target: str = ""
    severity: Literal["info", "warning", "error"] = "warning"
    evidence: str | None = None
    explanation: str = ""


class ExtractionCallResult(BaseModel):
    """Typed return for ONE extraction LLM call (one page, one doc type).

    Replaces mutable per-call agent state (`last_tables`). Passed explicitly
    through local and Temporal orchestration; never shared across pages.
    """

    doc_type: DocType
    page_number: int = Field(ge=1, default=1)
    fields: list[ExtractedField] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    new_field_names: list[str] = Field(default_factory=list)


class ResultCacheMetadata(BaseModel):
    """Provenance for a cache-reused page result."""

    fingerprint: str = ""
    computed_at: str | None = None
    original_timings: dict[str, float] = Field(default_factory=dict)
    acceptance_policy_version: str = ACCEPTANCE_POLICY_VERSION
    cache_lookup_ms: float = 0.0
    hit_type: Literal["full", "partial", "miss"] = "miss"


class RoutingDecision(BaseModel):
    doc_type: RouterDocType
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


class TableColumn(BaseModel):
    key: str
    label: str


class TableCell(BaseModel):
    column: str
    value: str | float | None = None
    # NOTE: default 0.0 (never 1.0) — missing model confidence must not
    # render as 100%. The UI labels this a model estimate.
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_span: str | None = None
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    acceptance: AcceptanceStatus = "unevaluated"


class ExtractedTable(BaseModel):
    name: str = "line_items"
    columns: list[TableColumn] = Field(default_factory=list)
    rows: list[list[TableCell]] = Field(default_factory=list)


class SourceReference(BaseModel):
    source_id: UUID
    filename: str
    page_number: int = Field(ge=1)
    page_count: int = Field(ge=1)
    preview_url: str | None = None
    download_url: str | None = None


class JobProgress(BaseModel):
    completed_pages: int = 0
    total_pages: int = 0
    stage: str = "queued"
    queue_seconds: float = 0


class ExtractionResult(BaseModel):
    """Result for ONE document. A single upload may yield several results
    (multi-page / multi-document files) — see FileExtractionResponse."""

    id: UUID = Field(default_factory=uuid4)
    doc_type: DocType
    source: SourceReference | None = None
    ocr_blocks: list[OCRBlock] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    usage: dict[str, dict[str, int]] = Field(default_factory=dict)
    judge_status: Literal["passed", "flagged", "skipped", "unavailable"] = "unavailable"
    language: str | None = None
    fields: list[ExtractedField] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    needs_review: bool = False
    completeness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    judge: JudgeResult | None = None
    routing_reason: str | None = None
    full_text: str | None = None
    error: str | None = None
    failed_stage: Literal["ocr", "router", "extractor", "validator", "judge"] | None = None
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
    # --- Accepted-data / review separation (backward-compatible defaults) ---
    # `fields`/`tables` hold ACCEPTED data only. Rejected candidates live
    # separately for review/debug and are excluded from normal exports.
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    review_issues: list[StructuredReviewIssue] = Field(default_factory=list)
    acceptance_status: AcceptanceStatus = "unevaluated"
    acceptance_policy_version: str = ACCEPTANCE_POLICY_VERSION
    # Registry-derived prompt version this result was computed with
    # (api/app/prompts/*.json — content hash, set at construction time).
    prompt_version: str = Field(default_factory=compound_prompt_version)
    cache_metadata: ResultCacheMetadata | None = None


class FileUploadMeta(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None


class FileExtractionResponse(BaseModel):
    request: FileUploadMeta
    documents: list[ExtractionResult] = Field(default_factory=list)
    error: str | None = None
    job_id: str | None = None
    file_errors: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)


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
    progress: JobProgress | None = None
    job_id: UUID
    status: str
    result: FileExtractionResponse | None = None
    error: str | None = None


ExtractionResult.model_rebuild()
