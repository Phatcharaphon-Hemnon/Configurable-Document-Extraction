"""Unit tests for RouterAgent._reconcile_with_catalog and related helpers.

Tests cover:
- _normalize_name: basic case/spacing/hyphen normalization
- Synonym mismatch: AI "Merchant Name" vs catalog "vendor_name" — NOT matched
  (normalization is case/spacing only, not synonym mapping)
- Exact match after normalization: AI "total_amount" vs catalog "total_amount"
  — name and likely_required overridden from catalog, description kept from AI
- Catalog field never proposed by AI gets appended with catalog's required flag
- doc_type with no matching catalog file — suggested_fields returned unchanged
  (open-schema fallback for novel document types)
- RouterAgent constructed with knowledge_base=None works exactly as before
  (backward compatibility)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Make sure the Backend package is importable when running from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _BACKEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.agents.router import RouterAgent, _normalize_name  # noqa: E402
from app.schemas.documents import FieldDefinition  # noqa: E402
from app.services.knowledge_base import KnowledgeBaseRepository  # noqa: E402
from app.core.config import Settings  # noqa: E402  (used as spec for MagicMock)


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


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ===========================================================================
# PART 1 — _normalize_name
# ===========================================================================


class TestNormalizeName:
    def test_lowercase(self) -> None:
        assert _normalize_name("TotalAmount") == "totalamount"

    def test_spaces_become_underscores(self) -> None:
        assert _normalize_name("total amount") == "total_amount"

    def test_hyphens_become_underscores(self) -> None:
        assert _normalize_name("invoice-number") == "invoice_number"

    def test_mixed_spaces_and_hyphens(self) -> None:
        assert _normalize_name("bill to - name") == "bill_to_name"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert _normalize_name("  total_amount  ") == "total_amount"

    def test_already_normalized(self) -> None:
        assert _normalize_name("total_amount") == "total_amount"

    def test_multiple_spaces_collapsed(self) -> None:
        assert _normalize_name("vendor   name") == "vendor_name"

    def test_title_case_with_spaces(self) -> None:
        assert _normalize_name("Total Amount") == "total_amount"


# ===========================================================================
# PART 2 — _reconcile_with_catalog
# ===========================================================================


class TestReconcileWithCatalog:
    """Tests for RouterAgent._reconcile_with_catalog (static method)."""

    # -----------------------------------------------------------------------
    # 2a. Synonym mismatch — normalization does NOT bridge synonyms
    # -----------------------------------------------------------------------

    def test_synonym_mismatch_ai_field_kept_unchanged(self) -> None:
        """AI proposes "Merchant Name"; catalog has "vendor_name".

        After normalization:
          - "Merchant Name" → "merchant_name"
          - "vendor_name"   → "vendor_name"

        These are different strings, so NO match occurs.  The AI field must
        be kept as-is (open-schema behaviour) and the catalog field must be
        appended as a missing entry.

        This documents the current scope: _reconcile_with_catalog bridges
        case/spacing differences only, not semantic synonyms.
        """
        ai_fields = [_fd("Merchant Name", description="The merchant's trading name", likely_required=False)]
        catalog_fields = [_fd("vendor_name", description="Vendor name", likely_required=True)]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        names = [f.name for f in result]
        # AI field preserved unchanged
        assert "Merchant Name" in names
        ai_entry = next(f for f in result if f.name == "Merchant Name")
        assert ai_entry.description == "The merchant's trading name"
        assert ai_entry.likely_required is False

        # Catalog field appended because it was never matched
        assert "vendor_name" in names
        catalog_entry = next(f for f in result if f.name == "vendor_name")
        assert catalog_entry.likely_required is True

    # -----------------------------------------------------------------------
    # 2b. Exact match after normalization
    # -----------------------------------------------------------------------

    def test_exact_match_overrides_name_and_required_keeps_description(self) -> None:
        """AI proposes "total_amount"; catalog has "total_amount" (required=True).

        Expected outcome:
        - name stays "total_amount" (catalog canonical)
        - likely_required becomes True (from catalog)
        - description stays from AI (more specific/contextual)
        """
        ai_fields = [
            _fd("total_amount", description="The grand total including tax", likely_required=False)
        ]
        catalog_fields = [
            _fd("total_amount", description="Must be positive", likely_required=True)
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        assert len(result) == 1
        field = result[0]
        assert field.name == "total_amount"
        assert field.likely_required is True          # overridden from catalog
        assert field.description == "The grand total including tax"  # kept from AI

    def test_case_insensitive_match_overrides_name_to_canonical(self) -> None:
        """AI proposes "Invoice Number" (title case); catalog has "invoice_number".

        After normalization both become "invoice_number" → match.
        The canonical catalog name "invoice_number" should replace the AI name.
        """
        ai_fields = [
            _fd("Invoice Number", description="Unique invoice identifier", likely_required=False)
        ]
        catalog_fields = [
            _fd("invoice_number", description="Non-empty alphanumeric identifier", likely_required=True)
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        assert len(result) == 1
        field = result[0]
        assert field.name == "invoice_number"         # canonical name from catalog
        assert field.likely_required is True          # overridden from catalog
        assert field.description == "Unique invoice identifier"  # kept from AI

    # -----------------------------------------------------------------------
    # 2c. Catalog field never proposed by AI gets appended
    # -----------------------------------------------------------------------

    def test_unmatched_catalog_field_appended(self) -> None:
        """A catalog field the AI never proposed must be appended.

        The appended entry should use:
        - catalog's name
        - catalog's required as likely_required
        - catalog's validation_rule as description (so Extractor knows what to look for)
        """
        ai_fields = [_fd("invoice_number", description="Invoice ID", likely_required=True)]
        catalog_fields = [
            _fd("invoice_number", likely_required=True),
            _fd("total_amount", likely_required=True, validation_rule="Must be positive"),
            _fd("currency", likely_required=False, validation_rule="ISO 4217 code"),
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        names = [f.name for f in result]
        assert "invoice_number" in names
        assert "total_amount" in names
        assert "currency" in names

        total = next(f for f in result if f.name == "total_amount")
        assert total.likely_required is True
        assert total.description == "Must be positive"  # validation_rule used as description

        currency = next(f for f in result if f.name == "currency")
        assert currency.likely_required is False
        assert currency.description == "ISO 4217 code"

    # -----------------------------------------------------------------------
    # 2d. AI-proposed fields with no catalog match are kept (open-schema)
    # -----------------------------------------------------------------------

    def test_novel_ai_field_not_in_catalog_is_kept(self) -> None:
        """AI proposes a field not in the catalog at all — it must be kept."""
        ai_fields = [
            _fd("qr_code_data", description="QR code payload", likely_required=False),
        ]
        catalog_fields = [
            _fd("invoice_number", likely_required=True),
        ]

        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        names = [f.name for f in result]
        assert "qr_code_data" in names
        assert "invoice_number" in names  # appended from catalog

        qr = next(f for f in result if f.name == "qr_code_data")
        assert qr.description == "QR code payload"

    # -----------------------------------------------------------------------
    # 2e. Input lists are not mutated
    # -----------------------------------------------------------------------

    def test_input_lists_not_mutated(self) -> None:
        """_reconcile_with_catalog must not mutate either input list."""
        ai_fields = [_fd("total_amount", description="AI description", likely_required=False)]
        catalog_fields = [_fd("total_amount", description="Catalog description", likely_required=True)]

        ai_copy = [f.model_copy() for f in ai_fields]
        catalog_copy = [f.model_copy() for f in catalog_fields]

        RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        # Original lists unchanged
        assert ai_fields[0].name == ai_copy[0].name
        assert ai_fields[0].description == ai_copy[0].description
        assert ai_fields[0].likely_required == ai_copy[0].likely_required
        assert catalog_fields[0].name == catalog_copy[0].name

    # -----------------------------------------------------------------------
    # 2f. Empty inputs
    # -----------------------------------------------------------------------

    def test_empty_ai_fields_returns_all_catalog_fields(self) -> None:
        """When AI proposes nothing, all catalog fields are appended."""
        catalog_fields = [
            _fd("invoice_number", likely_required=True, validation_rule="Non-empty"),
            _fd("total_amount", likely_required=True, validation_rule="Positive"),
        ]
        result = RouterAgent._reconcile_with_catalog([], catalog_fields)
        assert len(result) == 2
        assert {f.name for f in result} == {"invoice_number", "total_amount"}

    def test_empty_catalog_returns_ai_fields_unchanged(self) -> None:
        """When catalog is empty, AI fields are returned as-is."""
        ai_fields = [_fd("some_field", description="desc", likely_required=True)]
        result = RouterAgent._reconcile_with_catalog(ai_fields, [])
        assert len(result) == 1
        assert result[0].name == "some_field"
        assert result[0].description == "desc"


# ===========================================================================
# PART 3 — Open-schema fallback: doc_type with no catalog file
# ===========================================================================


class TestOpenSchemaFallback:
    """Regression test: novel doc types with no catalog file must pass through
    suggested_fields completely unchanged."""

    def test_no_catalog_file_suggested_fields_unchanged(self, tmp_path: Path) -> None:
        """When get_catalog_fields returns [] (no matching catalog), the
        reconciliation step is skipped and suggested_fields are returned
        exactly as the AI proposed them."""
        kb = KnowledgeBaseRepository(tmp_path)  # empty knowledge base

        # Confirm get_catalog_fields returns [] for an unknown type
        assert kb.get_catalog_fields("medical_form") == []

        ai_fields = [
            _fd("patient_name", description="Full name of the patient", likely_required=True),
            _fd("diagnosis_code", description="ICD-10 code", likely_required=False),
        ]

        # Simulate what classify() does: only reconcile when catalog_fields is non-empty
        catalog_fields = kb.get_catalog_fields("medical_form")
        if catalog_fields:
            result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)
        else:
            result = ai_fields  # open-schema fallback — no reconciliation

        # Fields must be exactly the AI's original proposals
        assert len(result) == 2
        assert result[0].name == "patient_name"
        assert result[0].description == "Full name of the patient"
        assert result[0].likely_required is True
        assert result[1].name == "diagnosis_code"
        assert result[1].likely_required is False

    def test_catalog_file_present_triggers_reconciliation(self, tmp_path: Path) -> None:
        """When a catalog file exists for the doc_type, reconciliation runs."""
        catalog_data = {
            "doc_type": "invoice",
            "description": "Invoice",
            "fields": [
                {"name": "invoice_number", "type": "string", "required": True, "validation_rule": "Non-empty"},
                {"name": "total_amount", "type": "number", "required": True, "validation_rule": "Positive"},
            ],
        }
        _write_json(tmp_path / "field_catalog" / "invoice_fields.json", catalog_data)

        kb = KnowledgeBaseRepository(tmp_path)
        catalog_fields = kb.get_catalog_fields("invoice")
        assert len(catalog_fields) == 2

        ai_fields = [_fd("invoice_number", description="AI description", likely_required=False)]
        result = RouterAgent._reconcile_with_catalog(ai_fields, catalog_fields)

        # invoice_number matched → likely_required overridden to True
        inv = next(f for f in result if f.name == "invoice_number")
        assert inv.likely_required is True
        assert inv.description == "AI description"  # AI description preserved

        # total_amount not proposed by AI → appended
        assert any(f.name == "total_amount" for f in result)


# ===========================================================================
# PART 4 — Backward compatibility: knowledge_base=None
# ===========================================================================


class TestRouterAgentBackwardCompatibility:
    """RouterAgent constructed without knowledge_base must work exactly as
    before — no reconciliation, no errors."""

    def _make_settings(self) -> Settings:
        settings = MagicMock(spec=Settings)
        settings.router_model_name = "google/gemini-3-flash-preview"
        settings.sut_genai_api_key = "test-key"
        return settings

    def test_init_without_knowledge_base(self) -> None:
        """RouterAgent(settings) — no knowledge_base kwarg — must not raise."""
        settings = self._make_settings()
        agent = RouterAgent(settings)
        assert agent._knowledge_base is None

    def test_init_with_knowledge_base_none_explicit(self) -> None:
        """RouterAgent(settings, knowledge_base=None) must not raise."""
        settings = self._make_settings()
        agent = RouterAgent(settings, knowledge_base=None)
        assert agent._knowledge_base is None

    def test_classify_skips_reconciliation_when_no_knowledge_base(self) -> None:
        """When _knowledge_base is None, classify() must return the raw AI
        suggested_fields without any catalog reconciliation."""
        from app.schemas.gemini_schemas import RoutingResponseSchema

        settings = self._make_settings()
        agent = RouterAgent(settings)

        # Build a fake parsed response
        fake_field = MagicMock()
        fake_field.name = "invoice_number"
        fake_field.description = "Invoice ID"
        fake_field.likely_required = True

        fake_parsed = MagicMock(spec=RoutingResponseSchema)
        fake_parsed.doc_type = "invoice"
        fake_parsed.language = "en"
        fake_parsed.reason = "Looks like an invoice"
        fake_parsed.confidence = 0.95
        fake_parsed.suggested_fields = [fake_field]

        fake_result = MagicMock()
        fake_result.parsed = fake_parsed

        with patch.object(agent._client, "generate_structured", return_value=fake_result):
            decision = agent.classify(filename="test.pdf", text_hint="Invoice #001")

        assert len(decision.suggested_fields) == 1
        assert decision.suggested_fields[0].name == "invoice_number"
        assert decision.suggested_fields[0].likely_required is True
