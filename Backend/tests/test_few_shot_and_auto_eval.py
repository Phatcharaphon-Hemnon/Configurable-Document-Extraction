"""Unit tests for few-shot prompting and auto-evaluation features.

Tests cover:
- get_few_shot_examples returns [] for an unknown doc_type
- get_few_shot_examples respects the size cap (_FEW_SHOT_MAX_CHARS)
- auto_evaluation is None when no ground-truth file matches
- auto_evaluation is populated correctly when a ground-truth file matches
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

from app.services.knowledge_base import KnowledgeBaseRepository, _cap_by_size, _FEW_SHOT_MAX_CHARS  # noqa: E402
from app.services.extraction_service import DocumentExtractionService  # noqa: E402
from app.schemas.documents import (  # noqa: E402
    ExtractionField,
    ExtractionResult,
    ValidationResult,
    JudgeResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kb(tmp_path: Path) -> KnowledgeBaseRepository:
    """Return a KnowledgeBaseRepository rooted at *tmp_path*."""
    return KnowledgeBaseRepository(tmp_path)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ===========================================================================
# PART 1 — Few-shot lookup
# ===========================================================================


class TestGetFewShotExamples:
    """Tests for KnowledgeBaseRepository.get_few_shot_examples."""

    def test_returns_empty_list_for_unknown_doc_type(self, tmp_path: Path) -> None:
        """Unknown doc_type → [] (no exception)."""
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("totally_unknown_type")
        assert result == []

    def test_returns_empty_list_when_few_shot_dir_missing(self, tmp_path: Path) -> None:
        """No few_shot directory at all → []."""
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("invoice")
        assert result == []

    def test_loads_examples_from_disk(self, tmp_path: Path) -> None:
        """Examples are loaded and only the 'fields' portion is kept."""
        example = {
            "description": "test invoice",
            "input_text": "some raw text",
            "output": {"invoice_number": "INV-001", "total": 100},
        }
        _write_json(tmp_path / "few_shot" / "invoice" / "example_01.json", example)
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("invoice")
        assert len(result) == 1
        assert result[0] == {"fields": {"invoice_number": "INV-001", "total": 100}}

    def test_case_insensitive_folder_match(self, tmp_path: Path) -> None:
        """doc_type matching is case-insensitive."""
        example = {"output": {"po_number": "PO-999"}}
        _write_json(tmp_path / "few_shot" / "PO" / "example_01.json", example)
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("po")
        assert len(result) == 1

    def test_respects_limit_parameter(self, tmp_path: Path) -> None:
        """limit= parameter caps the number of examples returned."""
        for i in range(5):
            _write_json(
                tmp_path / "few_shot" / "invoice" / f"example_0{i + 1}.json",
                {"output": {"invoice_number": f"INV-00{i}"}},
            )
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("invoice", limit=2)
        assert len(result) == 2

    def test_respects_size_cap(self, tmp_path: Path) -> None:
        """Total serialized size of returned examples must not exceed _FEW_SHOT_MAX_CHARS
        (unless a single example already exceeds the cap, in which case it is still
        returned — the cap only prevents *adding* more examples once the budget is full)."""
        # Create examples whose combined size exceeds the cap
        big_value = "x" * 400  # each example ~420 chars serialized
        for i in range(10):
            _write_json(
                tmp_path / "few_shot" / "invoice" / f"example_{i:02d}.json",
                {"output": {"field": big_value}},
            )
        kb = _make_kb(tmp_path)
        result = kb.get_few_shot_examples("invoice")
        total_chars = sum(len(json.dumps(ex, ensure_ascii=False, separators=(",", ":"))) for ex in result)
        assert total_chars <= _FEW_SHOT_MAX_CHARS

    def test_results_are_cached(self, tmp_path: Path) -> None:
        """Second call for the same doc_type does NOT re-read from disk."""
        example = {"output": {"invoice_number": "INV-001"}}
        example_path = tmp_path / "few_shot" / "invoice" / "example_01.json"
        _write_json(example_path, example)
        kb = _make_kb(tmp_path)

        first = kb.get_few_shot_examples("invoice")
        # Overwrite the file on disk — cached result should be returned unchanged
        example_path.write_text(json.dumps({"output": {"invoice_number": "CHANGED"}}), encoding="utf-8")
        second = kb.get_few_shot_examples("invoice")

        assert first == second  # cache hit — disk change not reflected

    def test_cap_by_size_helper_empty_input(self) -> None:
        """_cap_by_size on empty list returns empty list."""
        assert _cap_by_size([], 1500) == []

    def test_cap_by_size_helper_single_large_example(self) -> None:
        """A single example that exceeds the cap is still returned (never drop the only example)."""
        big = {"fields": {"x": "y" * 2000}}
        result = _cap_by_size([big], 100)
        assert result == [big]


# ===========================================================================
# PART 2 — Ground-truth lookup
# ===========================================================================


class TestGetGroundTruth:
    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        kb = _make_kb(tmp_path)
        assert kb.get_ground_truth("invoice_99") is None

    def test_returns_parsed_json_when_file_exists(self, tmp_path: Path) -> None:
        gt = {"invoice_number": "INV-001", "total": 500.0}
        _write_json(tmp_path / "ground_truth" / "invoice_01.json", gt)
        kb = _make_kb(tmp_path)
        result = kb.get_ground_truth("invoice_01")
        assert result == gt


# ===========================================================================
# PART 3 — Auto-evaluation in extraction pipeline
# ===========================================================================


def _make_service(tmp_path: Path) -> DocumentExtractionService:
    """Build a DocumentExtractionService with a mocked settings object."""
    settings = MagicMock()
    settings.knowledge_base_path = str(tmp_path)
    settings.few_shot_examples_per_doc_type = 3
    settings.recommended_extraction_model_name = "mock-model"
    return DocumentExtractionService(settings=settings)


def _make_extraction_result(extracted: dict, additional: dict | None = None) -> tuple:
    """Return (extracted_fields, additional_fields) as ExtractionField dicts."""
    ef = {k: ExtractionField(value=v, confidence=1.0) for k, v in extracted.items()}
    af = {k: ExtractionField(value=v, confidence=1.0) for k, v in (additional or {}).items()}
    return ef, af


class TestAutoEvaluation:
    """Tests for the auto-evaluation wiring in _extract_one_page."""

    def test_auto_evaluation_is_none_when_no_ground_truth(self, tmp_path: Path) -> None:
        """When no ground-truth file matches the uploaded filename, auto_evaluation is None."""
        service = _make_service(tmp_path)

        # Patch the heavy pipeline steps so we don't need real API keys
        routing = MagicMock()
        routing.doc_type = "invoice"
        routing.language = "en"
        routing.reason = "test"
        routing.suggested_fields = []

        extracted_fields = {"invoice_number": ExtractionField(value="INV-001", confidence=1.0)}
        additional_fields: dict = {}

        validation = ValidationResult(is_valid=True, completeness_score=1.0, issues=[])
        judge_result = JudgeResult(score=0.9, issues=[], notes="ok")

        with (
            patch.object(service, "_extract_text_from_image", return_value="some text"),
            patch.object(service.router, "classify", return_value=routing),
            patch.object(service.extractor, "extract", return_value=(extracted_fields, additional_fields)),
            patch.object(service.validator, "validate", return_value=validation),
            patch.object(service.judge, "evaluate", return_value=judge_result),
        ):
            result = service._extract_one_page(
                filename="no_match_document.pdf",
                image_bytes=b"fake",
                image_mime_type="image/png",
            )

        assert result.auto_evaluation is None

    def test_auto_evaluation_populated_when_ground_truth_matches(self, tmp_path: Path) -> None:
        """When a ground-truth file matches the filename stem, auto_evaluation is set."""
        # Write a ground-truth file that will match "invoice_01.pdf"
        gt = {
            "invoice_number": "INV-2023-1001",
            "vendor_name": "Bangkok Print & Packaging",
            "total": 1000.0,
        }
        _write_json(tmp_path / "ground_truth" / "invoice_01.json", gt)

        service = _make_service(tmp_path)

        routing = MagicMock()
        routing.doc_type = "invoice"
        routing.language = "en"
        routing.reason = "test"
        routing.suggested_fields = []

        # Prediction matches ground truth exactly → perfect score
        extracted_fields = {
            "invoice_number": ExtractionField(value="INV-2023-1001", confidence=1.0),
            "vendor_name": ExtractionField(value="Bangkok Print & Packaging", confidence=1.0),
            "total": ExtractionField(value=1000.0, confidence=1.0),
        }
        additional_fields: dict = {}

        validation = ValidationResult(is_valid=True, completeness_score=1.0, issues=[])
        judge_result = JudgeResult(score=0.95, issues=[], notes="ok")

        with (
            patch.object(service, "_extract_text_from_image", return_value="some text"),
            patch.object(service.router, "classify", return_value=routing),
            patch.object(service.extractor, "extract", return_value=(extracted_fields, additional_fields)),
            patch.object(service.validator, "validate", return_value=validation),
            patch.object(service.judge, "evaluate", return_value=judge_result),
        ):
            result = service._extract_one_page(
                filename="invoice_01.pdf",
                image_bytes=b"fake",
                image_mime_type="image/png",
            )

        assert result.auto_evaluation is not None
        assert result.auto_evaluation.f1 == pytest.approx(1.0)
        assert result.auto_evaluation.precision == pytest.approx(1.0)
        assert result.auto_evaluation.recall == pytest.approx(1.0)
        assert result.auto_evaluation.mismatches == []

    def test_auto_evaluation_partial_match(self, tmp_path: Path) -> None:
        """Partial prediction match produces correct F1 < 1.0."""
        gt = {
            "invoice_number": "INV-001",
            "vendor_name": "Acme Corp",
            "total": 500.0,
        }
        _write_json(tmp_path / "ground_truth" / "invoice_02.json", gt)

        service = _make_service(tmp_path)

        routing = MagicMock()
        routing.doc_type = "invoice"
        routing.language = "en"
        routing.reason = "test"
        routing.suggested_fields = []

        # Only one field matches
        extracted_fields = {
            "invoice_number": ExtractionField(value="INV-001", confidence=1.0),
            "vendor_name": ExtractionField(value="WRONG VENDOR", confidence=0.5),
        }
        additional_fields: dict = {}

        validation = ValidationResult(is_valid=True, completeness_score=0.5, issues=[])
        judge_result = JudgeResult(score=0.6, issues=[], notes="partial")

        with (
            patch.object(service, "_extract_text_from_image", return_value="text"),
            patch.object(service.router, "classify", return_value=routing),
            patch.object(service.extractor, "extract", return_value=(extracted_fields, additional_fields)),
            patch.object(service.validator, "validate", return_value=validation),
            patch.object(service.judge, "evaluate", return_value=judge_result),
        ):
            result = service._extract_one_page(
                filename="invoice_02.pdf",
                image_bytes=b"fake",
                image_mime_type="image/png",
            )

        assert result.auto_evaluation is not None
        assert result.auto_evaluation.f1 < 1.0
        # "total" is in GT but not in prediction → false negative
        # "vendor_name" is wrong → mismatch
        assert len(result.auto_evaluation.mismatches) >= 1

    def test_auto_evaluation_uses_evaluate_method_not_judge(self, tmp_path: Path) -> None:
        """Auto-evaluation must call self.evaluate(), NOT self.judge.evaluate()."""
        gt = {"invoice_number": "INV-001"}
        _write_json(tmp_path / "ground_truth" / "invoice_03.json", gt)

        service = _make_service(tmp_path)

        routing = MagicMock()
        routing.doc_type = "invoice"
        routing.language = "en"
        routing.reason = "test"
        routing.suggested_fields = []

        extracted_fields = {"invoice_number": ExtractionField(value="INV-001", confidence=1.0)}
        additional_fields: dict = {}

        validation = ValidationResult(is_valid=True, completeness_score=1.0, issues=[])
        judge_result = JudgeResult(score=0.9, issues=[], notes="ok")

        with (
            patch.object(service, "_extract_text_from_image", return_value="text"),
            patch.object(service.router, "classify", return_value=routing),
            patch.object(service.extractor, "extract", return_value=(extracted_fields, additional_fields)),
            patch.object(service.validator, "validate", return_value=validation),
            patch.object(service.judge, "evaluate", return_value=judge_result) as mock_judge_eval,
            patch.object(service, "evaluate", wraps=service.evaluate) as mock_local_eval,
        ):
            result = service._extract_one_page(
                filename="invoice_03.pdf",
                image_bytes=b"fake",
                image_mime_type="image/png",
            )

        # Local evaluate() was called once for auto-evaluation
        mock_local_eval.assert_called_once()
        # judge.evaluate() was called once (for the normal judge step) — not twice
        mock_judge_eval.assert_called_once()
        assert result.auto_evaluation is not None
