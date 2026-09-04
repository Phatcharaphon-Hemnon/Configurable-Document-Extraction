"""Validator agent: deterministic checks — no LLM call.

Produces `validation_errors` (strings) and the completeness score that feed
`needs_review`. Hallucination guard: every non-null value must have a
source_span that appears in the source document text.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.security import check_evidence
from app.schemas.documents import ExtractedField
from app.services.date_formats import KNOWN_DATE_FORMATS
from app.services.field_catalog import FieldCatalog

LOW_CONFIDENCE_THRESHOLD = 0.6


def _try_parse_date(value: str) -> datetime | None:
    s = value.strip()
    if not s or re.fullmatch(r"\d+", s):
        return None
    for fmt in KNOWN_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class ValidatorAgent:
    def __init__(self, catalog: FieldCatalog) -> None:
        self.catalog = catalog

    def validate(
        self,
        doc_type: str,
        fields: list[ExtractedField],
        document_text: str | None = None,
        is_image_extraction: bool = False,
    ) -> tuple[list[str], float, bool]:
        """Returns (validation_errors, completeness_score, needs_review)."""
        errors: list[str] = []

        known = self.catalog.get_fields(doc_type)
        required_names = [f.name for f in known if f.required]
        found_names = {f.name for f in fields}

        # 1) Required catalog fields present?
        missing = [n for n in required_names if n not in found_names]
        for name in missing:
            errors.append(f"Missing required field: {name}")

        # 2) Required fields with empty values?
        by_name = {f.name: f for f in fields}
        for name in required_names:
            field = by_name.get(name)
            if field is not None and field.value in (None, ""):
                errors.append(f"Required field has empty value: {name}")

        # 3) Hallucination guard — evidence must exist in the document.
        for field in fields:
            problem = check_evidence(
                field.name,
                field.value,
                field.source_span,
                document_text,
                is_image_extraction=is_image_extraction,
            )
            if problem:
                errors.append(problem)

        # 4) Low-confidence fields.
        for field in fields:
            if field.value is not None and field.confidence < LOW_CONFIDENCE_THRESHOLD:
                errors.append(f"Low confidence ({field.confidence:.2f}) on {field.name}")

        # 5) Date-typed catalog fields should parse as dates.
        type_by_name = {f.name: (f.type or "string") for f in known}
        for field in fields:
            if type_by_name.get(field.name) == "date" and isinstance(field.value, str):
                if _try_parse_date(field.value) is None:
                    errors.append(f"Unparseable date for {field.name}: {field.value!r}")

        # Completeness = required fields found with values / total required.
        if required_names:
            filled_required = sum(
                1
                for n in required_names
                if (by_name.get(n) is not None and by_name[n].value not in (None, ""))
            )
            completeness = filled_required / len(required_names)
        else:
            completeness = 1.0

        needs_review = bool(errors)
        return errors, completeness, needs_review
