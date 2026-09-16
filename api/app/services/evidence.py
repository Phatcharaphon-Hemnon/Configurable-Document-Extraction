"""Page-local evidence resolution backed by OCR blocks/spans.

Model responses carry only quoted ``source_span`` text. This module resolves
those quotes against the current page's OCR blocks/text in backend code and
returns :class:`EvidenceReference` objects with stored geometry.

Never trusts model-generated coordinates, IDs, row associations, or offsets:
geometry always comes from the stored OCR blocks. Supports multiple
references for legitimate multiline values. Preserves mappings when text is
normalized or sanitized (resolution tries raw, NFC-normalized, and
OCR-spacing-collapsed variants).
"""

from __future__ import annotations

from app.core.security import (
    collapse_ocr_spacing,
    normalize_evidence,
    strip_wrapping_quotes,
)
from app.schemas.documents import EvidenceReference
from app.schemas.ocr import OCRBlock


def assign_block_ids(blocks: list[OCRBlock], page_number: int) -> list[OCRBlock]:
    """Assign stable per-page block IDs in place (`page-{n}-block-{i}`).

    Idempotent: blocks that already carry an ID keep it. Returns the same
    list for chaining. IDs are page-local — never reused across pages.
    """
    for i, block in enumerate(blocks):
        if not getattr(block, "block_id", None):
            try:
                block.block_id = f"page-{page_number}-block-{i}"
            except Exception:
                continue
    return blocks


def _block_matches(block_text: str, needle: str) -> bool:
    """True when the normalized needle appears in the block text."""
    hay = normalize_evidence(block_text)
    if needle and needle in hay:
        return True
    return bool(needle) and needle in normalize_evidence(collapse_ocr_spacing(block_text))


def resolve_evidence(
    source_span: str | None,
    *,
    page_number: int,
    blocks: list[OCRBlock] | None,
    page_text: str | None,
    role: str | None = None,
) -> list[EvidenceReference]:
    """Resolve a quoted span to page-local references.

    - Splits multiline spans and resolves each non-empty line separately
      (legitimate multiline values keep multiple references).
    - Each line matches against OCR blocks first (preserving box/engine);
      lines found only in the joined page text get a text-level reference
      with no box.
    - Returns [] when nothing resolves (caller treats as unsupported).
    """
    if not source_span or not source_span.strip():
        return []
    span = strip_wrapping_quotes(source_span)
    lines = [ln.strip() for ln in span.splitlines() if ln.strip()] or [span.strip()]
    refs: list[EvidenceReference] = []
    blocks = list(blocks or [])
    for line in lines:
        needle = normalize_evidence(strip_wrapping_quotes(line))
        if not needle:
            continue
        matched = False
        for block in blocks:
            try:
                if _block_matches(block.text, needle):
                    refs.append(
                        EvidenceReference(
                            block_id=getattr(block, "block_id", None),
                            page_number=page_number,
                            subspan=line,
                            box=block.box,
                            engine=getattr(block, "engine", None),
                            role=role,  # type: ignore[arg-type]
                        )
                    )
                    matched = True
                    break
            except Exception:
                continue
        if matched:
            continue
        # Fall back to page-text level (no geometry).
        hay = normalize_evidence(page_text or "")
        if needle in hay or needle in normalize_evidence(collapse_ocr_spacing(page_text or "")):
            refs.append(
                EvidenceReference(
                    block_id=None,
                    page_number=page_number,
                    subspan=line,
                    box=None,
                    engine=None,
                    role=role,  # type: ignore[arg-type]
                )
            )
    return refs


def label_evidence_span(
    field_name: str,
    source_span: str | None,
    *,
    page_number: int,
    blocks: list[OCRBlock] | None,
    page_text: str | None,
) -> list[EvidenceReference]:
    """Resolve with role='label' (printed caption evidence)."""
    return resolve_evidence(
        source_span, page_number=page_number, blocks=blocks, page_text=page_text, role="label"
    )


def value_evidence_span(
    field_name: str,
    source_span: str | None,
    *,
    page_number: int,
    blocks: list[OCRBlock] | None,
    page_text: str | None,
) -> list[EvidenceReference]:
    """Resolve with role='value' (populated value evidence)."""
    return resolve_evidence(
        source_span, page_number=page_number, blocks=blocks, page_text=page_text, role="value"
    )
