"""Security helpers: prompt-injection defense and hallucination guards.

Document content is DATA, never instructions. Every piece of document text
that enters an LLM prompt must pass through :func:`sanitize_document_text`.
"""

from __future__ import annotations

import re

# Patterns commonly used to smuggle instructions through document text.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a|an|the)\s+", re.IGNORECASE),
    re.compile(r"new\s+(instructions?|role|persona)\s*:", re.IGNORECASE),
    re.compile(r"system\s*(prompt|message)\s*:", re.IGNORECASE),
    re.compile(r"</?(system|assistant|user|instructions?)>", re.IGNORECASE),
    re.compile(r"(reveal|show|print|repeat)\s+(your|the)\s+(system\s+)?(prompt|instructions)", re.IGNORECASE),
    re.compile(r"extract\s+and\s+(email|send|post)\s+", re.IGNORECASE),
    re.compile(r"api[_\s-]?key|secret[_\s-]?key|password\s*[:=]", re.IGNORECASE),
)

# Replacement marker for neutralized content.
_REDACTED = "[redacted-injection-attempt]"

# Hard cap on document text embedded into prompts (token control).
MAX_PROMPT_CHARS = 12_000


def sanitize_document_text(text: str | None) -> str:
    """Neutralize instruction-like content inside document text.

    - Replaces known injection phrasings with a redaction marker.
    - Strips markdown/HTML role tags.
    - Collapses whitespace and enforces a length cap (token minimization).
    """
    if not text:
        return ""

    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > MAX_PROMPT_CHARS:
        cleaned = cleaned[:MAX_PROMPT_CHARS] + " …[truncated]"
    return cleaned


def is_suspicious(text: str | None) -> bool:
    """True when the text contains at least one injection pattern."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def check_evidence(
    field_name: str,
    value: object,
    source_span: str | None,
    document_text: str | None,
    is_image_extraction: bool = False,
) -> str | None:
    """Hallucination guard: verify an extracted value is backed by evidence.

    Returns a human-readable problem string, or None when the field is OK.
    A field is flagged when it has no source_span at all, or when the
    source_span does not appear (normalized) in the document text.

    For image extractions (is_image_extraction=True), we trust the source_span
    provided by the vision model since it reads the image directly, not OCR text.
    """
    if value is None:
        return None  # absent fields are validated elsewhere

    # Always require source_span evidence
    if not source_span or not source_span.strip():
        return f"{field_name}: no source_span evidence provided"

    # For image extractions, trust the source_span if provided
    # The vision model reads the image directly, so source_span reflects
    # what it actually saw, not OCR output
    if is_image_extraction:
        return None

    # For text extractions, validate source_span against document text
    if document_text:
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip().lower()

        span, doc = _norm(str(source_span)), _norm(document_text)
        # Evidence must overlap the document; allow substring OR token overlap.
        if span not in doc:
            span_tokens = set(span.split())
            doc_tokens = set(doc.split())
            overlap = span_tokens & doc_tokens
            if len(span_tokens) == 0 or len(overlap) / max(len(span_tokens), 1) < 0.75:
                return f"{field_name}: source_span not found in document (possible hallucination)"
    return None
