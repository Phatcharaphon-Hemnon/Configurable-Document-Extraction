import json
from pathlib import Path

from app.schemas.documents import DocumentLanguage, DocumentType, FieldDefinition, TemplateSchema


class KnowledgeBaseRepository:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path

    def list_templates(self) -> list[TemplateSchema]:
        templates = [
            TemplateSchema(
                doc_type=DocumentType.INVOICE,
                description="Invoice extraction schema",
                language_support=[DocumentLanguage.EN, DocumentLanguage.TH],
                fields=[
                    FieldDefinition(name="invoice_number", type="string", required=True, validation_rule="Non-empty identifier"),
                    FieldDefinition(name="total_amount", type="number", required=True, validation_rule="Must be positive"),
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
        return templates

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
