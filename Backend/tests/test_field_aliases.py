"""Tests for the alternative-names field-matching system.

Covers:
- build_alternative_name_lookup: canonical + alternative names resolve correctly.
- DocumentExtractionService.evaluate() with doc_type scores synonym-matched
  prediction as a perfect match (F1 == 1.0).
- DocumentExtractionService.evaluate() WITHOUT doc_type keeps exact-match
  behaviour (backward compatibility).
- At least one alternative-name test per doc_type (invoice, po, delivery_note).
- RouterAgent._reconcile_with_catalog with alternative_names bridges synonym gaps.
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

from app.services.field_matching import _normalize_name, build_alternative_name_lookup  # noqa: E402
from app.agents.router import RouterAgent  # noqa: E402
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
    alternative_names: list[str] | None = None,
) -> FieldDefinition:
    """Shorthand FieldDefinition factory."""
    return FieldDefinition(
        name=name,
        description=description,
        likely_required=likely_required,
        validation_rule=validation_rule,
        alternative_names=alternative_names or [],
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
    settings.openrouter_api_key = "test-key"
    settings.llm_request_timeout_seconds = 90.0
    settings.few_shot_examples_per_doc_type = 0
    settings.recommended_extraction_model_name = "test"
    settings.recommended_extraction_model_display_name = "Test"
    settings.recommended_extraction_model_reason = "Test"
    return DocumentExtractionService(settings=settings)


# ===========================================================================
# PART 1 — build_alternative_name_lookup unit tests
# ===========================================================================


class TestBuildAlternativeNameLookup:
    """Tests for the build_alternative_name_lookup function."""

    # -- Invoice alternatives -----------------------------------------------

    def test_invoice_order_date_resolves_to_invoice_date(self) -> None:
        catalog = [_fd("invoice_date", alternative_names=["order_date", "issue_date", "date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["order_date"].name == "invoice_date"

    def test_invoice_issue_date_resolves_to_invoice_date(self) -> None:
        catalog = [_fd("invoice_date", alternative_names=["order_date", "issue_date", "date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["issue_date"].name == "invoice_date"

    def test_invoice_date_resolves_to_invoice_date(self) -> None:
        catalog = [_fd("invoice_date", alternative_names=["order_date", "issue_date", "date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["date"].name == "invoice_date"

    def test_invoice_due_date_resolves_to_payment_due_date(self) -> None:
        catalog = [_fd("payment_due_date", alternative_names=["due_date", "payment_due"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["due_date"].name == "payment_due_date"

    def test_invoice_payment_due_resolves_to_payment_due_date(self) -> None:
        catalog = [_fd("payment_due_date", alternative_names=["due_date", "payment_due"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["payment_due"].name == "payment_due_date"

    def test_invoice_vendor_name_resolves_to_seller_name(self) -> None:
        catalog = [_fd("seller_name", alternative_names=["vendor_name", "merchant_name"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["vendor_name"].name == "seller_name"

    def test_invoice_merchant_name_resolves_to_seller_name(self) -> None:
        catalog = [_fd("seller_name", alternative_names=["vendor_name", "merchant_name"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["merchant_name"].name == "seller_name"

    def test_invoice_grand_total_resolves_to_total_amount(self) -> None:
        catalog = [_fd("total_amount", alternative_names=["grand_total", "amount_due"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["grand_total"].name == "total_amount"

    def test_invoice_vat_resolves_to_tax_amount(self) -> None:
        catalog = [_fd("tax_amount", alternative_names=["vat", "gst"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["vat"].name == "tax_amount"

    def test_invoice_invoice_no_resolves_to_invoice_number(self) -> None:
        catalog = [_fd("invoice_number", alternative_names=["invoice_no", "inv_no"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["invoice_no"].name == "invoice_number"

    def test_invoice_bill_to_resolves_to_bill_to_name(self) -> None:
        catalog = [_fd("bill_to_name", alternative_names=["bill_to", "customer_name"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["bill_to"].name == "bill_to_name"

    def test_invoice_gst_id_resolves_to_tax_id(self) -> None:
        catalog = [_fd("tax_id", alternative_names=["gst_id", "vat_id"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["gst_id"].name == "tax_id"

    def test_invoice_change_resolves_to_change_amount(self) -> None:
        catalog = [_fd("change_amount", alternative_names=["change", "change_due"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["change"].name == "change_amount"

    def test_invoice_tendered_amount_resolves_to_paid_amount(self) -> None:
        catalog = [_fd("paid_amount", alternative_names=["tendered_amount", "amount_tendered"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["tendered_amount"].name == "paid_amount"

    def test_invoice_subtotal_resolves_to_subtotal_amount(self) -> None:
        catalog = [_fd("subtotal_amount", alternative_names=["subtotal", "net_amount"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["subtotal"].name == "subtotal_amount"

    # -- PO alternatives ----------------------------------------------------

    def test_po_po_date_resolves_to_order_date(self) -> None:
        catalog = [_fd("order_date", alternative_names=["po_date", "issue_date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["po_date"].name == "order_date"

    def test_po_issue_date_resolves_to_order_date(self) -> None:
        catalog = [_fd("order_date", alternative_names=["po_date", "issue_date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["issue_date"].name == "order_date"

    def test_po_vendor_name_resolves_to_supplier_name(self) -> None:
        catalog = [_fd("supplier_name", alternative_names=["vendor_name", "seller_name"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["vendor_name"].name == "supplier_name"

    def test_po_ship_to_resolves_to_buyer_name(self) -> None:
        catalog = [_fd("buyer_name", alternative_names=["ship_to", "bill_to"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["ship_to"].name == "buyer_name"

    def test_po_grand_total_resolves_to_total_amount(self) -> None:
        catalog = [_fd("total_amount", alternative_names=["grand_total", "total"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["grand_total"].name == "total_amount"

    def test_po_terms_resolves_to_payment_terms(self) -> None:
        catalog = [_fd("payment_terms", alternative_names=["terms", "pay_terms", "net_terms"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["terms"].name == "payment_terms"

    def test_po_po_no_resolves_to_po_number(self) -> None:
        catalog = [_fd("po_number", alternative_names=["po_no", "po_id"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["po_no"].name == "po_number"

    # -- Delivery note alternatives -----------------------------------------

    def test_dn_do_number_resolves_to_delivery_note_number(self) -> None:
        catalog = [_fd("delivery_note_number", alternative_names=["do_number", "dn_no"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["do_number"].name == "delivery_note_number"

    def test_dn_dn_no_resolves_to_delivery_note_number(self) -> None:
        catalog = [_fd("delivery_note_number", alternative_names=["do_number", "dn_no"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["dn_no"].name == "delivery_note_number"

    def test_dn_courier_resolves_to_delivered_by(self) -> None:
        catalog = [_fd("delivered_by", alternative_names=["courier", "carrier"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["courier"].name == "delivered_by"

    def test_dn_ship_to_resolves_to_recipient_name(self) -> None:
        catalog = [_fd("recipient_name", alternative_names=["ship_to", "deliver_to"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["ship_to"].name == "recipient_name"

    def test_dn_shipper_resolves_to_sender_name(self) -> None:
        catalog = [_fd("sender_name", alternative_names=["ship_from", "shipper"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["shipper"].name == "sender_name"

    def test_dn_gross_weight_resolves_to_total_weight(self) -> None:
        catalog = [_fd("total_weight", alternative_names=["gross_weight", "weight"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["gross_weight"].name == "total_weight"

    def test_dn_remarks_resolves_to_notes(self) -> None:
        catalog = [_fd("notes", alternative_names=["remarks", "comments"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["remarks"].name == "notes"

    # -- Pass-through for unknown names ------------------------------------

    def test_unknown_name_not_in_lookup(self) -> None:
        """A name not in any alternative_names should not be in the lookup."""
        catalog = [_fd("invoice_date", alternative_names=["order_date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert "qr_code_data" not in lookup

    def test_canonical_name_in_lookup(self) -> None:
        """The canonical name itself should always be in the lookup."""
        catalog = [_fd("invoice_date", alternative_names=["order_date"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["invoice_date"].name == "invoice_date"

    def test_empty_alternative_names(self) -> None:
        """A field with no alternative_names should still have its canonical in the lookup."""
        catalog = [_fd("currency")]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["currency"].name == "currency"
        assert len(lookup) == 1

    def test_normalization_applied_to_alternatives(self) -> None:
        """Alternative names should be normalized before lookup."""
        catalog = [_fd("seller_name", alternative_names=["Vendor Name", "MERCHANT-NAME"])]
        lookup = build_alternative_name_lookup(catalog)
        assert lookup["vendor_name"].name == "seller_name"
        assert lookup["merchant_name"].name == "seller_name"


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
# PART 3 — RouterAgent._reconcile_with_catalog with alternative_names
# ===========================================================================


class TestReconcileWithCatalogAndAlternativeNames:
    """_reconcile_with_catalog with alternative_names should bridge synonym gaps."""

    def test_order_date_matches_invoice_date_via_alternative_names(self) -> None:
        """AI proposes 'Order Date'; catalog has 'invoice_date' with
        alternative_names=['order_date'].

        With alternative_names:
          - 'Order Date' normalizes to 'order_date'
          - 'order_date' is in catalog_lookup via alternative_names
          - Matches catalog entry 'invoice_date'
        """
        ai_fields = [
            _fd("Order Date", description="Date the order was placed", likely_required=False),
        ]
        catalog_fields = [
            _fd("invoice_date", description="Date in any recognisable format",
                likely_required=True, alternative_names=["order_date", "issue_date"]),
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        assert len(result) == 1
        field = result[0]
        assert field.name == "invoice_date"  # canonical from catalog
        assert field.likely_required is True  # overridden from catalog
        assert field.description == "Date the order was placed"  # kept from AI

    def test_synonym_without_alternative_names_still_unmatched(self) -> None:
        """Without alternative_names, 'Order Date' should NOT match 'invoice_date'."""
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
        catalog_fields = [_fd("supplier_name", description="Non-empty string",
                              likely_required=True, alternative_names=["vendor_name", "seller_name"])]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        assert len(result) == 1
        assert result[0].name == "supplier_name"
        assert result[0].likely_required is True

    def test_dn_courier_matches_delivered_by(self) -> None:
        """delivery_note: AI 'Courier' should match catalog 'delivered_by'."""
        ai_fields = [_fd("Courier", description="Shipping carrier", likely_required=False)]
        catalog_fields = [_fd("delivered_by", description="Non-empty string",
                              likely_required=True, alternative_names=["courier", "carrier"])]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

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
            _fd("invoice_date", likely_required=True,
                alternative_names=["order_date", "issue_date", "date"]),
            _fd("payment_due_date", likely_required=False,
                alternative_names=["due_date", "payment_due"]),
            _fd("seller_name", likely_required=True,
                alternative_names=["vendor_name", "merchant_name"]),
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        names = [f.name for f in result]
        assert "invoice_date" in names
        assert "payment_due_date" in names
        assert "seller_name" in names
        # All catalog fields matched — nothing appended
        assert len(result) == 3

    def test_net_terms_resolves_to_payment_terms(self) -> None:
        """PO: AI 'Net Terms' should match catalog 'payment_terms'."""
        ai_fields = [_fd("Net Terms", description="Payment terms", likely_required=False)]
        catalog_fields = [_fd("payment_terms", description="Non-empty string",
                              likely_required=False, alternative_names=["terms", "pay_terms", "net_terms"])]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        assert len(result) == 1
        assert result[0].name == "payment_terms"


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
        suggested = [_fd("invoice_date", alternative_names=["order_date", "issue_date"])]
        extracted, additional = _parse_extraction_response(parsed, suggested, doc_type="invoice")

        assert "invoice_date" in extracted
        assert extracted["invoice_date"].value == "2026-01-01"
        assert "random_extra" in additional

    def test_empty_fields_returns_empty_dicts(self) -> None:
        from app.agents.extractors import _parse_extraction_response
        from app.schemas.llm_schemas import ExtractionResponseSchema

        parsed = ExtractionResponseSchema(fields=[])
        suggested = [_fd("invoice_date", alternative_names=["order_date"])]
        extracted, additional = _parse_extraction_response(parsed, suggested, doc_type="invoice")

        assert extracted == {}
        assert additional == {}
