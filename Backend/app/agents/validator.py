from Backend.app.schemas.documents import DocumentType, ExtractionField, ValidationIssue, ValidationResult
from datetime import datetime


class ValidatorAgent:
    def validate(self, doc_type: DocumentType, fields: dict[str, ExtractionField]) -> ValidationResult:
        issues: list[ValidationIssue] = []

        if doc_type == DocumentType.INVOICE:
            required = ["invoice_number", "balance_due", "currency"]
            self._validate_required(fields, required, issues)
            self._validate_positive_amount(fields, issues)
            self._validate_date_like(fields, ["statement_date", "payment_due_date"], issues)
        elif doc_type == DocumentType.PURCHASE_ORDER:
            required = ["po_number", "supplier_name", "order_date"]
            self._validate_required(fields, required, issues)
            self._validate_date_like(fields, ["order_date", "delivery_date"], issues)
            self._validate_positive_amount(fields, issues)
        elif doc_type == DocumentType.DELIVERY_NOTE:
            required = ["delivery_note_number", "delivered_by", "delivery_date"]
            self._validate_required(fields, required, issues)
            self._validate_date_like(fields, ["delivery_date"], issues)

        return ValidationResult(is_valid=not issues, issues=issues)

    def _validate_required(
        self,
        fields: dict[str, ExtractionField],
        required_fields: list[str],
        issues: list[ValidationIssue],
    ) -> None:
        for field_name in required_fields:
            if field_name not in fields:
                issues.append(ValidationIssue(field=field_name, message="Missing required field"))

    def _validate_positive_amount(self, fields: dict[str, ExtractionField], issues: list[ValidationIssue]) -> None:
        amount = fields.get("balance_due") or fields.get("total_amount")
        if amount is None:
            return
        if isinstance(amount.value, (int, float)) and amount.value <= 0:
            issues.append(ValidationIssue(field="total_amount", message="Total amount must be positive"))

    def _validate_date_like(self, fields: dict[str, ExtractionField], field_names: list[str], issues: list[ValidationIssue]) -> None:
        for field_name in field_names:
            field = fields.get(field_name)
            if field is None or field.value is None:
                continue
            if isinstance(field.value, str) and self._parse_date(field.value) is None:
                issues.append(ValidationIssue(field=field_name, message="Date does not match a supported format"))

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
