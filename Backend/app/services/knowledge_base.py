import json
from pathlib import Path

from Backend.app.schemas.documents import DocumentLanguage, DocumentType, FieldDefinition, TemplateSchema


class KnowledgeBaseRepository:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path

    def list_templates(self) -> list[TemplateSchema]:
        # Load instructor-provided field catalogs from disk.
        # If files are missing, fall back to a minimal hardcoded catalog.
        base = self.base_path / "field_catalog"
        templates: list[TemplateSchema] = []

        def _load(path: Path) -> dict[str, object] | None:
            if not path.exists():
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None

        invoice = _load(base / "invoice_fields.json")
        po = _load(base / "po_fields.json")
        dn = _load(base / "delivery_note_fields.json")

        mapping: list[tuple[DocumentType, dict[str, object] | None]] = [
            (DocumentType.INVOICE, invoice),
            (DocumentType.PURCHASE_ORDER, po),
            (DocumentType.DELIVERY_NOTE, dn),
        ]

        for doc_type, payload in mapping:
            if not payload:
                continue
            fields_payload = payload.get("fields")
            if not isinstance(fields_payload, list):
                continue
            fields: list[FieldDefinition] = []
            for f in fields_payload:
                if not isinstance(f, dict):
                    continue
                name = str(f.get("name", "")).strip()
                f_type = str(f.get("type", "string")).strip()
                required = bool(f.get("required", True))
                rule = str(f.get("validation_rule", "")).strip()
                if name:
                    fields.append(FieldDefinition(name=name, type=f_type, required=required, validation_rule=rule))

            templates.append(
                TemplateSchema(
                    doc_type=doc_type,
                    description=str(payload.get("description", "")),
                    language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                    fields=fields,
                )
            )

        if templates:
            return templates

        # Minimal fallback
        return [
            TemplateSchema(
                doc_type=DocumentType.INVOICE,
                description="Invoice extraction schema",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="invoice_number", type="string", required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="balance_due", type="number", required=True, validation_rule="Must be positive"),
                    FieldDefinition(name="currency", type="string", required=True, validation_rule="ISO currency code"),
                ],
            ),
            TemplateSchema(
                doc_type=DocumentType.PURCHASE_ORDER,
                description="Purchase order extraction schema",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="po_number", type="string", required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="supplier_name", type="string", required=True, validation_rule="Non-empty value"),
                    FieldDefinition(name="order_date", type="date", required=True, validation_rule="ISO 8601 date"),
                ],
            ),
            TemplateSchema(
                doc_type=DocumentType.DELIVERY_NOTE,
                description="Delivery note extraction schema",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="delivery_note_number", type="string", required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="delivered_by", type="string", required=True, validation_rule="Non-empty value"),
                    FieldDefinition(name="delivery_date", type="date", required=True, validation_rule="ISO 8601 date"),
                ],
            ),
        ]

    def sample_summary(self) -> dict[str, object]:
        if not self.base_path.exists():
            return {"documents": 0, "ground_truth": 0, "examples_per_doc_type": 0}

        documents = len(list(self.base_path.glob("documents/*")))
        ground_truth = len(list(self.base_path.glob("ground_truth/*.json")))
        examples = len(list(self.base_path.glob("few_shot/**/*.json")))
        return {
            "documents": documents,
            "ground_truth": ground_truth,
            "examples_per_doc_type": examples,
        }
