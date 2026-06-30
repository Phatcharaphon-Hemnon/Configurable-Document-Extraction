from app.schemas.documents import DocumentType, ExtractionField, ValidationIssue, ValidationResult


class ValidatorAgent:
    def validate(self, doc_type: DocumentType, fields: dict[str, ExtractionField]) -> ValidationResult:
        issues: list[ValidationIssue] = []

        if doc_type == DocumentType.INVOICE:
            required = ["invoice_number", "total_amount", "currency"]
            self._validate_required(fields, required, issues)
            self._validate_positive_amount(fields, issues)
        elif doc_type == DocumentType.PURCHASE_ORDER:
            required = ["po_number", "supplier_name", "order_date"]
            self._validate_required(fields, required, issues)
        elif doc_type == DocumentType.DELIVERY_NOTE:
            required = ["delivery_note_number", "delivered_by", "delivery_date"]
            self._validate_required(fields, required, issues)

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
        total_amount = fields.get("total_amount")
        if total_amount is None:
            return
        if isinstance(total_amount.value, (int, float)) and total_amount.value <= 0:
            issues.append(ValidationIssue(field="total_amount", message="Total amount must be positive"))
