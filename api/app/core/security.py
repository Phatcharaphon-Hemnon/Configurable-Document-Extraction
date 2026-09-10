"""Security helpers: prompt-injection defense and hallucination guards.

Document content is DATA, never instructions. Every piece of document text
that enters an LLM prompt must pass through :func:`sanitize_document_text`.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

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

    cleaned = re.sub(r"[^\S\n]+", " ", cleaned).strip()
    if len(cleaned) > MAX_PROMPT_CHARS:
        cleaned = cleaned[:MAX_PROMPT_CHARS] + " …[truncated]"
    return cleaned


def is_suspicious(text: str | None) -> bool:
    """True when the text contains at least one injection pattern."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def strip_wrapping_quotes(span: str) -> str:
    """Remove one layer of wrapping quotes the LLM adds around spans.

    The extractor prompt demands a bare verbatim quote, but models often
    return `"10256"` or `'S00012726'`. The literal quote chars are never
    part of the document text, so a strict substring check would flag a
    correct value as a hallucination. Only strips when the span starts
    AND ends with a matching quote char and has content inside.

    Also unescapes JSON string escapes (`\\n`, `\\t`) the model emits
    inside multi-line spans — the document text holds real newlines.
    """
    s = span.strip()
    pairs = {'"': '"', "'": "'", "`": "`", "[": "]", "(": ")"}
    if len(s) >= 2 and s[0] in pairs and s[-1] == pairs[s[0]]:
        inner = s[1:-1].strip()
        if inner:
            s = inner
    return s.replace("\\n", "\n").replace("\\t", " ").replace('\\"', '"')


def collapse_ocr_spacing(text: str) -> str:
    """Remove spaces the OCR engine inserts inside tokens.

    Tesseract frequently splits tokens (`"201 6-07-15"` for `2016-07-15`,
    `"Oty"` stays as-is but digit splits are common). Collapsing
    digit-adjacent whitespace lets evidence match despite that noise.
    Word boundaries between letters are preserved.
    """
    return re.sub(r"(?<=\d)\s+(?=\d)", "", text)


def token_overlap(a: str, b: str) -> float:
    """Fraction of a's tokens present in b (unordered, OCR-noise fallback)."""
    tokens_a = set(normalize_evidence(a).split())
    if not tokens_a:
        return 0.0
    tokens_b = set(normalize_evidence(b).split())
    return len(tokens_a & tokens_b) / len(tokens_a)


def is_verbatim_span(span: str, document_text: str) -> bool:
    """True when the (quote-stripped) span is a contiguous document substring.

    Tries both the normalized text and the OCR-spacing-collapsed variant.
    """
    needle = normalize_evidence(strip_wrapping_quotes(span))
    if not needle:
        return False
    haystack = normalize_evidence(document_text)
    if needle in haystack:
        return True
    return needle in normalize_evidence(collapse_ocr_spacing(document_text))


def _parse_date_any(text: str):
    """Parse common printed/ISO date forms; None when not a date."""
    from datetime import datetime

    s = text.strip()
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
        "%m/%d/%Y", "%m-%d-%Y",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _dates_in_text(text: str) -> list:
    """Extract candidate date substrings from a longer span and parse them."""
    candidates = re.findall(
        r"\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2}|\d{4}\.\d{1,2}\.\d{1,2}"
        r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
        r"|\d{1,2} [A-Za-z]+ \d{4}|[A-Za-z]+ \d{1,2}, \d{4}",
        text,
    )
    parsed = []
    for cand in candidates:
        dt = _parse_date_any(cand)
        if dt is not None:
            parsed.append(dt)
    return parsed


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

    Strictness rules (all in the safe direction — flag, never drop):
    - Wrapping quotes around spans are stripped before comparison.
    - Short spans (<= 3 tokens) must be a contiguous normalized substring;
      the unordered token-overlap fallback applies only to longer spans as
      an OCR-noise tolerance.
    - Empty/None document text never passes: fields carrying a value are
      flagged as unverifiable.
    - ``is_image_extraction`` is kept for caller compatibility but no longer
      bypasses verification — spans are always checked against OCR text
      when text exists.
    """
    if value is None:
        return None  # absent fields are validated elsewhere

    # Always require source_span evidence
    if not source_span or not source_span.strip():
        return f"{field_name}: no source_span evidence provided"

    span = strip_wrapping_quotes(source_span)

    if not document_text:
        return f"{field_name}: no document text to verify against (possible hallucination)"

    if is_verbatim_span(span, document_text):
        pass
    elif len(normalize_evidence(span).split()) <= 3:
        return f"{field_name}: source_span not found in document (possible hallucination)"
    elif token_overlap(span, document_text) < 0.75 and token_overlap(
        collapse_ocr_spacing(span), collapse_ocr_spacing(document_text)
    ) < 0.75:
        return f"{field_name}: source_span not found in document (possible hallucination)"
    if not value_in_text(value, span):
        return f"{field_name}: value not supported by source_span (possible hallucination)"
    return None


def normalize_evidence(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()


def value_in_text(value: object, text: str) -> bool:
    """Compare complete strings or numeric magnitudes; no synonyms or fuzzy token overlap."""
    if value is None:
        return True
    span = strip_wrapping_quotes(text)
    raw_value_text = str(value)
    value_text = normalize_evidence(strip_wrapping_quotes(raw_value_text)
                                    if isinstance(value, str) else raw_value_text)
    normalized = normalize_evidence(span)
    # Date-aware comparison: the extractor normalizes printed dates to ISO
    # ("15/07/2016" -> "2016-07-15"). A verbatim string check would flag a
    # correct normalization, so compare parsed dates when both sides parse.
    value_date = _parse_date_any(value_text)
    if value_date is not None:
        variants = {normalized, normalize_evidence(collapse_ocr_spacing(span))}
        for variant in variants:
            if _parse_date_any(variant) == value_date:
                return True
            if value_date in _dates_in_text(variant):
                return True
    # Numeric coercion is allowed for actual numbers and decimal-formatted strings,
    # never leading-zero identifiers.
    numeric = isinstance(value, (int, float)) or bool(re.fullmatch(r"-?\d+[,.]\d[\d,.]*", value_text))
    if numeric:
        try:
            expected = Decimal(value_text.replace(",", ""))
            for variant in {normalized, normalize_evidence(collapse_ocr_spacing(span))}:
                tokens = re.findall(r"(?<![\w.])-?\d[\d,]*(?:[.,]\d+)?(?![\w.])", variant)
                for token in tokens:
                    candidates = {token.replace(",", "")}
                    # Comma-as-decimal-separator ("153,50" == 153.5): a single
                    # comma followed by exactly 2 digits is a decimal mark,
                    # not a thousands separator.
                    if re.fullmatch(r"\d+,\d{2}", token):
                        candidates.add(token.replace(",", "."))
                    if any(Decimal(c) == expected for c in candidates):
                        return True
                # OCR dot-drop ("87 45" for 87.45): re-inserting exactly one
                # decimal point must recover the expected magnitude. Only the
                # dot form is tried — never the thousands form ("1 200" must
                # not match 12.00).
                for token in re.findall(r"(?<![\w.])-?\d{1,3}(?: \d{2,3})+(?![\w.])", variant):
                    try:
                        if Decimal(token.replace(" ", ".")) == expected:
                            return True
                    except InvalidOperation:
                        continue
            return False
        except InvalidOperation:
            return False
    for variant in {normalized, normalize_evidence(collapse_ocr_spacing(span))}:
        if re.search(r"(?<!\w)" + re.escape(value_text) + r"(?!\w)", variant):
            return True
    return False
