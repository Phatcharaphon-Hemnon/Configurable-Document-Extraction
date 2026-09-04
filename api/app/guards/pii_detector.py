"""PII detection for extracted field values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PIIType(str, Enum):
    """Types of PII to detect."""

    CREDIT_CARD = "credit_card"
    NATIONAL_ID = "national_id"
    EMAIL = "email"
    PHONE = "phone"
    BANK_ACCOUNT = "bank_account"
    TAX_ID = "tax_id"


@dataclass
class PIIMatch:
    """A detected PII match."""

    pii_type: PIIType
    value: str
    start: int
    end: int
    confidence: float


# PII detection patterns
PII_PATTERNS: dict[PIIType, dict] = {
    PIIType.CREDIT_CARD: {
        "pattern": r"\b(?:\d[ -]*?){13,16}\b",
        "description": "Credit card number",
        "redact": True,
    },
    PIIType.NATIONAL_ID: {
        "pattern": r"\b\d{1}-\d{4}-\d{5}-\d{2}-\d{1}\b",  # Thai National ID
        "description": "National ID number",
        "redact": True,
    },
    PIIType.EMAIL: {
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "description": "Email address",
        "redact": False,
    },
    PIIType.PHONE: {
        "pattern": r"\b(?:\+?66|0)\d{8,9}\b",  # Thai phone numbers
        "description": "Phone number",
        "redact": False,
    },
    PIIType.TAX_ID: {
        "pattern": r"\b\d{13}\b",  # Thai Tax ID (13 digits)
        "description": "Tax ID number",
        "redact": True,
    },
}


class PIIDetector:
    """Detect PII in text and field values.

    Example:
        detector = PIIDetector()
        matches = detector.detect("Invoice total: 4111-1111-1111-1111")
        # matches contains PIMatch(pii_type=PIIType.CREDIT_CARD, ...)

        redacted = detector.redact("Tax ID: 1234567890123")
        # redacted == "Tax ID: [REDACTED_TAX_ID]"
    """

    def __init__(self, redact_pii: bool = False):
        """Initialize PII detector.

        Args:
            redact_pii: If True, automatically redact detected PII.
        """
        self.redact_pii = redact_pii
        self._compiled_patterns = {
            pii_type: re.compile(info["pattern"], re.IGNORECASE)
            for pii_type, info in PII_PATTERNS.items()
        }

    def detect(self, text: str) -> list[PIIMatch]:
        """Detect all PII in text.

        Args:
            text: Text to scan for PII.

        Returns:
            List of detected PII matches.
        """
        matches: list[PIIMatch] = []

        for pii_type, pattern in self._compiled_patterns.items():
            for match in pattern.finditer(text):
                matches.append(
                    PIIMatch(
                        pii_type=pii_type,
                        value=match.group(),
                        start=match.start(),
                        end=match.end(),
                        confidence=0.9,
                    )
                )

        return matches

    def redact(self, text: str) -> str:
        """Redact detected PII from text.

        Args:
            text: Text to redact PII from.

        Returns:
            Text with PII replaced by redaction markers.
        """
        matches = sorted(self.detect(text), key=lambda m: m.start, reverse=True)

        for match in matches:
            if PII_PATTERNS[match.pii_type]["redact"]:
                redacted = f"[REDACTED_{match.pii_type.value.upper()}]"
                text = text[: match.start] + redacted + text[match.end :]

        return text

    def check_field_value(
        self, field_name: str, value: str | None
    ) -> list[PIIMatch]:
        """Check a field value for PII.

        Args:
            field_name: Name of the field (for context).
            value: Field value to check.

        Returns:
            List of detected PII matches.
        """
        if not value or not isinstance(value, str):
            return []

        return self.detect(value)

    def has_pii(self, text: str) -> bool:
        """Check if text contains any PII.

        Args:
            text: Text to check.

        Returns:
            True if PII is detected.
        """
        return len(self.detect(text)) > 0

    def get_pii_types(self, text: str) -> set[PIIType]:
        """Get unique PII types found in text.

        Args:
            text: Text to scan.

        Returns:
            Set of PII types found.
        """
        return {match.pii_type for match in self.detect(text)}
