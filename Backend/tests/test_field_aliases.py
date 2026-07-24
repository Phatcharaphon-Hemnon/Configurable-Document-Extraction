"""Tests for the field-alias resolution system.

Covers:
- resolve_field_alias: known synonyms resolve, unknown names pass through.
- DocumentExtractionService.evaluate() with doc_type scores synonym-matched
  prediction as a perfect match (F1 == 1.0).
- DocumentExtractionService.evaluate() WITHOUT doc_type keeps exact-match
  behaviour (backward compatibility).
- At least one alias test per doc_type (invoice, po, delivery_note).
- RouterAgent._reconcile_with_catalog with doc_type bridges synonym gaps.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Make sure the Backend package is importable when running from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _BACKEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.services.field_aliases import FIELD_ALIASES, resolve_field_alias  # noqa: E402
from app.agents.router import RouterAgent, _normalize_name  # noqa: E402
from app.schemas.documents import FieldDefinition  # noqa: E402
from app.core.config import Settings  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fd(
    name: str,
    description: str | None = None,
    likely_required: bool = False,
    validation_rule: str | None = None,
) -> FieldDefinition:
    """Shorthand FieldDefinition factory."""
    return FieldDefinition(
        name=name,
        description=description,
        likely_required=likely_required,
        validation_rule=validation_rule,
    )


def _make_service():
    """Create a DocumentExtractionService with mocked external dependencies."""
    from app.services.extraction_service import DocumentExtractionService

    settings = MagicMock(spec=Settings)
    settings.knowledge_base_path = str(
        Path(__file__).resolve().parents[1] / "app" / "data" / "knowledge_base"
    )
    settings.llama_cloud_api_key = "fake-key"
    settings.schema_mode = "open"
    settings.router_model_name = "test-model"
    settings.github_models_token = "test-key"
    settings.few_shot_examples_per_doc_type = 0
    settings.recommended_extraction_model_name = "test"
    settings.recommended_extraction_model_display_name = "Test"
    settings.recommended_extraction_model_reason = "Test"
    return DocumentExtractionService(settings=settings)


# ===========================================================================
# PART 1 — resolve_field_alias unit tests
# ===========================================================================


class TestResolveFieldAlias:
    """Tests for the resolve_field_alias function."""

    # -- Invoice aliases ---------------------------------------------------

    def test_invoice_order_date_resolves_to_invoice_date(self) -> None:
        assert resolve_field_alias("invoice", "order_date") == "invoice_date"

    def test_invoice_issue_date_resolves_to_invoice_date(self) -> None:
        assert resolve_field_alias("invoice", "issue_date") == "invoice_date"

    def test_invoice_date_resolves_to_invoice_date(self) -> None:
        assert resolve_field_alias("invoice", "date") == "invoice_date"

    def test_invoice_due_date_resolves_to_payment_due_date(self) -> None:
        assert resolve_field_alias("invoice", "due_date") == "payment_due_date"

    def test_invoice_payment_due_resolves_to_payment_due_date(self) -> None:
        assert resolve_field_alias("invoice", "payment_due") == "payment_due_date"

    def test_invoice_vendor_name_resolves_to_seller_name(self) -> None:
        assert resolve_field_alias("invoice", "vendor_name") == "seller_name"

    def test_invoice_merchant_name_resolves_to_seller_name(self) -> None:
        assert resolve_field_alias("invoice", "merchant_name") == "seller_name"

    def test_invoice_grand_total_resolves_to_total_amount(self) -> None:
        assert resolve_field_alias("invoice", "grand_total") == "total_amount"

    def test_invoice_vat_resolves_to_tax_amount(self) -> None:
        assert resolve_field_alias("invoice", "vat") == "tax_amount"

    def test_invoice_invoice_no_resolves_to_invoice_number(self) -> None:
        assert resolve_field_alias("invoice", "invoice_no") == "invoice_number"

    def test_invoice_bill_to_resolves_to_bill_to_name(self) -> None:
        assert resolve_field_alias("invoice", "bill_to") == "bill_to_name"

    # -- PO aliases --------------------------------------------------------

    def test_po_po_date_resolves_to_order_date(self) -> None:
        assert resolve_field_alias("po", "po_date") == "order_date"

    def test_po_issue_date_resolves_to_order_date(self) -> None:
        assert resolve_field_alias("po", "issue_date") == "order_date"

    def test_po_vendor_name_resolves_to_supplier_name(self) -> None:
        assert resolve_field_alias("po", "vendor_name") == "supplier_name"

    def test_po_ship_to_resolves_to_buyer_name(self) -> None:
        assert resolve_field_alias("po", "ship_to") == "buyer_name"

    def test_po_grand_total_resolves_to_total_amount(self) -> None:
        assert resolve_field_alias("po", "grand_total") == "total_amount"

    def test_po_terms_resolves_to_payment_terms(self) -> None:
        assert resolve_field_alias("po", "terms") == "payment_terms"

    def test_po_po_no_resolves_to_po_number(self) -> None:
        assert resolve_field_alias("po", "po_no") == "po_number"

    # -- Delivery note aliases ---------------------------------------------

    def test_dn_do_number_resolves_to_delivery_note_number(self) -> None:
        assert resolve_field_alias("delivery_note", "do_number") == "delivery_note_number"

    def test_dn_dn_no_resolves_to_delivery_note_number(self) -> None:
        assert resolve_field_alias("delivery_note", "dn_no") == "delivery_note_number"

    def test_dn_courier_resolves_to_delivered_by(self) -> None:
        assert resolve_field_alias("delivery_note", "courier") == "delivered_by"

    def test_dn_ship_to_resolves_to_recipient_name(self) -> None:
        assert resolve_field_alias("delivery_note", "ship_to") == "recipient_name"

    def test_dn_shipper_resolves_to_sender_name(self) -> None:
        assert resolve_field_alias("delivery_note", "shipper") == "sender_name"

    def test_dn_gross_weight_resolves_to_total_weight(self) -> None:
        assert resolve_field_alias("delivery_note", "gross_weight") == "total_weight"

    def test_dn_remarks_resolves_to_notes(self) -> None:
        assert resolve_field_alias("delivery_note", "remarks") == "notes"

    # -- Pass-through for unknown names ------------------------------------

    def test_unknown_name_passed_through(self) -> None:
        """A name not in the alias table should be returned unchanged."""
        assert resolve_field_alias("invoice", "qr_code_data") == "qr_code_data"

    def test_unknown_doc_type_passed_through(self) -> None:
        """An unknown doc_type should return the input unchanged."""
        assert resolve_field_alias("receipt", "order_date") == "order_date"

    def test_case_insensitive_doc_type(self) -> None:
        """doc_type lookup is case-insensitive."""
        assert resolve_field_alias("INVOICE", "order_date") == "invoice_date"
        assert resolve_field_alias("Invoice", "due_date") == "payment_due_date"

    def test_canonical_name_not_double_resolved(self) -> None:
        """A canonical catalog name should not be an alias key (no loops)."""
        assert resolve_field_alias("invoice", "invoice_date") == "invoice_date"
        assert resolve_field_alias("po", "order_date") == "order_date"
        assert resolve_field_alias("delivery_note", "delivery_note_number") == "delivery_note_number"


# ===========================================================================
# PART 2 — evaluate() with doc_type (synonym reconciliation)
# ===========================================================================


class TestEvaluateWithDocType:
    """evaluate() with doc_type should reconcile field-name synonyms."""

    def test_invoice_synonym_match_perfect_score(self) -> None:
        """prediction uses {order_date, due_date} and ground_truth uses
        {invoice_date, payment_due_date} for the same values.
        With doc_type='invoice', this should score F1 == 1.0.
        """
        svc = _make_service()
        prediction = {"order_date": "2024-01-15", "due_date": "2024-02-15"}
        ground_truth = {"invoice_date": "2024-01-15", "payment_due_date": "2024-02-15"}

        result = svc.evaluate(prediction, ground_truth, doc_type="invoice")

        assert result.f1 == 1.0
        assert result.precision == 1.0
        assert result.recall == 1.0
        assert result.mismatches == []

    def test_invoice_synonym_match_without_doc_type_is_mismatch(self) -> None:
        """The same prediction/ground_truth WITHOUT doc_type should still
        produce mismatches (backward-compatible exact-match behaviour).
        """
        svc = _make_service()
        prediction = {"order_date": "2024-01-15", "due_date": "2024-02-15"}
        ground_truth = {"invoice_date": "2024-01-15", "payment_due_date": "2024-02-15"}

        result = svc.evaluate(prediction, ground_truth)

        # Without reconciliation, all fields are mismatches
        assert result.f1 < 1.0
        assert len(result.mismatches) > 0

    def test_po_synonym_match_perfect_score(self) -> None:
        """PO: prediction uses po_date, ground_truth uses order_date."""
        svc = _make_service()
        prediction = {"po_date": "2024-03-01", "terms": "Net 30"}
        ground_truth = {"order_date": "2024-03-01", "payment_terms": "Net 30"}

        result = svc.evaluate(prediction, ground_truth, doc_type="po")

        assert result.f1 == 1.0
        assert result.mismatches == []

    def test_delivery_note_synonym_match_perfect_score(self) -> None:
        """delivery_note: prediction uses do_number, ground_truth uses
        delivery_note_number."""
        svc = _make_service()
        prediction = {"do_number": "DN-001", "courier": "DHL"}
        ground_truth = {"delivery_note_number": "DN-001", "delivered_by": "DHL"}

        result = svc.evaluate(prediction, ground_truth, doc_type="delivery_note")

        assert result.f1 == 1.0
        assert result.mismatches == []

    def test_mixed_synonym_and_exact_match(self) -> None:
        """Some fields match exactly, others via synonym — all should score."""
        svc = _make_service()
        prediction = {
            "invoice_number": "INV-001",
            "order_date": "2024-01-15",
            "grand_total": "1000.00",
        }
        ground_truth = {
            "invoice_number": "INV-001",
            "invoice_date": "2024-01-15",
            "total_amount": "1000.00",
        }

        result = svc.evaluate(prediction, ground_truth, doc_type="invoice")

        assert result.f1 == 1.0
        assert result.mismatches == []

    def test_unmatched_prediction_field_stays_as_false_positive(self) -> None:
        """A prediction field with no synonym match should remain a FP."""
        svc = _make_service()
        prediction = {
            "invoice_number": "INV-001",
            "mystery_field": "unexpected",
        }
        ground_truth = {
            "invoice_number": "INV-001",
            "total_amount": "500.00",
        }

        result = svc.evaluate(prediction, ground_truth, doc_type="invoice")

        assert result.f1 < 1.0
        # total_amount is a false negative, mystery_field is a false positive

    def test_none_doc_type_same_as_no_doc_type(self) -> None:
        """doc_type=None should behave identically to not passing it."""
        svc = _make_service()
        prediction = {"order_date": "2024-01-15"}
        ground_truth = {"invoice_date": "2024-01-15"}

        result_none = svc.evaluate(prediction, ground_truth, doc_type=None)
        result_no = svc.evaluate(prediction, ground_truth)

        assert result_none.f1 == result_no.f1
        assert result_none.precision == result_no.precision
        assert result_none.recall == result_no.recall


# ===========================================================================
# PART 3 — RouterAgent._reconcile_with_catalog with doc_type
# ===========================================================================


class TestReconcileWithCatalogAndDocType:
    """_reconcile_with_catalog with doc_type should bridge synonym gaps."""

    def test_order_date_matches_invoice_date_with_doc_type(self) -> None:
        """AI proposes 'Order Date'; catalog has 'invoice_date'.

        With doc_type='invoice':
          - 'Order Date' normalizes to 'order_date'
          - resolve_field_alias('invoice', 'order_date') → 'invoice_date'
          - Matches catalog entry 'invoice_date'

        This follows the style of test_reconcile_with_catalog.py.
        """
        ai_fields = [
            _fd("Order Date", description="Date the order was placed", likely_required=False),
        ]
        catalog_fields = [
            _fd("invoice_date", description="Date in any recognisable format", likely_required=True),
        ]

        result = RouterAgent._reconcile_with_catalog(
            ai_fields, catalog_fields, doc_type="invoice"
        )

        assert len(result) == 1
        field = result[0]
        assert field.name == "invoice_date"  # canonical from catalog
        assert field.likely_required is True  # overridden from catalog
        assert field.description == "Date the order was placed"  # kept from AI

    def test_synonym_without_doc_type_still_unmatched(self) -> None:
        """Without doc_type, 'Order Date' should NOT match 'invoice_date'.

        This ensures backward compatibility — the default doc_type=''
        produces no alias resolution.
        """
        ai_fields = [
            _fd("Order Date", description="Date the order was placed", likely_required=False),
        ]
        catalog_fields = [
            _fd("invoice_date", description="Date in any recognisable format", likely_required=True),
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        names = [f.name for f in result]
        # AI field kept unchanged (no match)
        assert "Order Date" in names
        # Catalog field appended as unmatched
        assert "invoice_date" in names
        assert len(result) == 2

    def test_po_vendor_name_matches_supplier_name(self) -> None:
        """PO: AI 'vendor_name' should match catalog 'supplier_name'."""
        ai_fields = [_fd("vendor_name", description="The vendor", likely_required=False)]
        catalog_fields = [_fd("supplier_name", description="Non-empty string", likely_required=True)]

        result = RouterAgent._reconcile_with_catalog(
            ai_fields, catalog_fields, doc_type="po"
        )

        assert len(result) == 1
        assert result[0].name == "supplier_name"
        assert result[0].likely_required is True

    def test_dn_courier_matches_delivered_by(self) -> None:
        """delivery_note: AI 'Courier' should match catalog 'delivered_by'."""
        ai_fields = [_fd("Courier", description="Shipping carrier", likely_required=False)]
        catalog_fields = [_fd("delivered_by", description="Non-empty string", likely_required=True)]

        result = RouterAgent._reconcile_with_catalog(
            ai_fields, catalog_fields, doc_type="delivery_note"
        )

        assert len(result) == 1
        assert result[0].name == "delivered_by"
        assert result[0].likely_required is True
        assert result[0].description == "Shipping carrier"

    def test_multiple_synonyms_all_resolve(self) -> None:
        """Multiple AI fields with synonyms should all resolve to catalog."""
        ai_fields = [
            _fd("Order Date", description="Date", likely_required=False),
            _fd("Due Date", description="Payment due", likely_required=False),
            _fd("Merchant Name", description="Seller", likely_required=False),
        ]
        catalog_fields = [
            _fd("invoice_date", likely_required=True),
            _fd("payment_due_date", likely_required=False),
            _fd("seller_name", likely_required=True),
        ]

        result = RouterAgent._reconcile_with_catalog(
            ai_fields, catalog_fields, doc_type="invoice"
        )

        names = [f.name for f in result]
        assert "invoice_date" in names
        assert "payment_due_date" in names
        assert "seller_name" in names
        # All catalog fields matched — nothing appended
        assert len(result) == 3


class TestParseExtractionResponse:
    """Tests for _parse_extraction_response in extractors.py."""

    def test_alias_matching(self) -> None:
        from app.agents.extractors import _parse_extraction_response
        from app.schemas.llm_schemas import ExtractionResponseSchema, ExtractedFieldEntry

        parsed = ExtractionResponseSchema(
            fields=[
                ExtractedFieldEntry(name="order_date", value="2026-01-01", confidence=0.9, source_span="2026-01-01"),
                ExtractedFieldEntry(name="random_extra", value="extra val", confidence=0.8, source_span="extra val"),
            ]
        )
        suggested = {"invoice_date"}
        extracted, additional = _parse_extraction_response(parsed, suggested, doc_type="invoice")

        assert "invoice_date" in extracted
        assert extracted["invoice_date"].value == "2026-01-01"
        assert "random_extra" in additional

    def test_empty_fields_returns_empty_dicts(self) -> None:
        from app.agents.extractors import _parse_extraction_response
        from app.schemas.llm_schemas import ExtractionResponseSchema

        parsed = ExtractionResponseSchema(fields=[])
        extracted, additional = _parse_extraction_response(parsed, {"invoice_date"}, doc_type="invoice")

        assert extracted == {}
        assert additional == {}

