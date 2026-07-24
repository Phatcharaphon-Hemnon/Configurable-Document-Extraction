from __future__ import annotations

from datetime import datetime

from app.schemas.documents import ExtractionField, FieldDefinition, ValidationIssue, ValidationResult

_AMOUNT_HINTS = ("amount", "total", "balance", "price", "sum", "due")
_DATE_HINTS = ("date",)


class ValidatorAgent:
    """Open-schema, non-blocking validator.

    There is no hardcoded required-field list per document type anymore.
    Instead:
      - A field is only treated as "required" for THIS document if the
        Router marked it likely_required=true when proposing suggested_fields.
      - Missing a likely_required field, or a document with literally zero
        extracted fields, is the only thing that can make needs_review=true.
      - Missing any other (non-required) suggested field is informational
        only — it lowers completeness_score but is never an "error".
      - Generic pattern checks (based on field NAME, not a fixed catalog)
        still run: fields whose name suggests a date are checked for a
        parseable format; fields whose name suggests a monetary amount are
        checked for being a positive number when present.
    """

    def validate(
        self,
        suggested_fields: list[FieldDefinition],
        extracted_fields: dict[str, ExtractionField],
        additional_fields: dict[str, ExtractionField] | None = None,
        schema_mode: str = "open",
        catalog_fields: list[FieldDefinition] | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        additional_fields = additional_fields or {}
        all_fields: dict[str, ExtractionField] = {**extracted_fields, **additional_fields}

        # --- Hard-ish check: nothing extracted at all ---
        if not all_fields:
            issues.append(
                ValidationIssue(
                    field="*",
                    message="No fields could be extracted from this document at all",
                    severity="error",
                )
            )

        # --- Missing required fields ---
        if schema_mode == "strict":
            if catalog_fields is not None:
                required_names = [f.name for f in catalog_fields if f.likely_required]
            else:
                required_names = [f.name for f in suggested_fields if f.likely_required]
        else:
            required_names = [f.name for f in suggested_fields if f.likely_required]

        for name in required_names:
            if name not in extracted_fields:
                msg = (
                    f"Missing required field '{name}' from catalog"
                    if schema_mode == "strict"
                    else "Missing field the router flagged as required for this document"
                )
                issues.append(
                    ValidationIssue(
                        field=name,
                        message=msg,
                        severity="warning",
                    )
                )

        # --- Generic pattern checks ---
        severity_format = "warning"
        for name, item in all_fields.items():
            lower = name.lower()
            if item.value is None:
                continue
            if any(hint in lower for hint in _DATE_HINTS):
                if isinstance(item.value, str) and self._parse_date(item.value) is None:
                    issues.append(
                        ValidationIssue(
                            field=name,
                            message="Date does not match a supported format",
                            severity=severity_format,
                        )
                    )
            if any(hint in lower for hint in _AMOUNT_HINTS):
                if isinstance(item.value, (int, float)) and item.value < 0:
                    issues.append(
                        ValidationIssue(
                            field=name,
                            message="Amount should not be negative",
                            severity=severity_format,
                        )
                    )

        completeness_score = self._completeness_score(suggested_fields, extracted_fields)

        # Only "error" severity issues make the document invalid; "warning"
        # severity is informational and does not block anything.
        has_hard_error = any(i.severity == "error" for i in issues)

        return ValidationResult(
            is_valid=not has_hard_error,
            completeness_score=completeness_score,
            issues=issues,
        )

    def _completeness_score(
        self,
        suggested_fields: list[FieldDefinition],
        extracted_fields: dict[str, ExtractionField],
    ) -> float:
        if not suggested_fields:
            # No fields were suggested at all — completeness is measured by
            # whether we found anything, capped at 1.0 either way.
            return 1.0 if extracted_fields else 0.0
        found = sum(1 for f in suggested_fields if f.name in extracted_fields)
        return round(found / len(suggested_fields), 4)

    def _parse_date(self, value: str):
        value = value.strip()
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d/%m/%y",
            "%m/%d/%y",
            "%d-%m-%Y",
            "%m-%d-%Y",
            "%d-%m-%y",
            "%m-%d-%y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None