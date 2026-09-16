"""Tests for AI guardrails."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.guards.audit_logger import AuditLogger, SecurityEventType
from app.guards.content_guard import (
    ContentLimits,
    validate_document_text,
    validate_field_name,
    validate_field_value,
    validate_source_span,
)
from app.guards.output_guard import (
    OutputValidationError,
    clean_llm_output,
    detect_suspicious_output,
    sanitize_error_message,
    validate_output_schema,
)
from app.guards.pii_detector import PIIDetector, PIIType
from app.guards.rate_limiter import RateLimitConfig, RateLimiter
from app.guards.timeout_guard import (
    StageTimeoutConfig,
    StageTimeoutError,
    TimeoutGuard,
    stage_timeout,
)

# ============================================================================
# Rate Limiter Tests
# ============================================================================


class TestRateLimiter:
    def test_allows_requests_within_limit(self):
        limiter = RateLimiter(RateLimitConfig(max_requests=5, window_seconds=60))
        for _ in range(5):
            allowed, _ = limiter.check_rate_limit("client1")
            assert allowed is True
            limiter.record_request("client1")

    def test_blocks_requests_exceeding_limit(self):
        limiter = RateLimiter(RateLimitConfig(max_requests=3, window_seconds=60))
        for _ in range(3):
            limiter.check_rate_limit("client1")
            limiter.record_request("client1")

        allowed, reason = limiter.check_rate_limit("client1")
        assert allowed is False
        assert "Rate limit exceeded" in reason

    def test_concurrent_limit(self):
        limiter = RateLimiter(RateLimitConfig(max_concurrent=2))
        limiter.record_request("client1")
        limiter.record_request("client1")

        allowed, reason = limiter.check_rate_limit("client1")
        assert allowed is False
        assert "Concurrent" in reason

    def test_record_completion_reduces_concurrent(self):
        limiter = RateLimiter(RateLimitConfig(max_concurrent=2))
        limiter.record_request("client1")
        limiter.record_request("client1")
        limiter.record_completion("client1")

        allowed, _ = limiter.check_rate_limit("client1")
        assert allowed is True

    def test_separate_clients_independent(self):
        limiter = RateLimiter(RateLimitConfig(max_requests=2))
        limiter.record_request("client1")
        limiter.record_request("client1")

        allowed, _ = limiter.check_rate_limit("client2")
        assert allowed is True

    def test_get_retry_after(self):
        limiter = RateLimiter(RateLimitConfig(max_requests=2, window_seconds=60))
        limiter.record_request("client1")
        limiter.record_request("client1")

        retry_after = limiter.get_retry_after("client1")
        assert retry_after > 0

    def test_reset_client(self):
        limiter = RateLimiter(RateLimitConfig(max_requests=2))
        limiter.record_request("client1")
        limiter.record_request("client1")

        limiter.reset_client("client1")
        allowed, _ = limiter.check_rate_limit("client1")
        assert allowed is True


# ============================================================================
# Content Guard Tests
# ============================================================================


class TestContentGuard:
    def test_validate_document_text_within_limit(self):
        text = "A" * 1000
        result = validate_document_text(text)
        assert result == text

    def test_validate_document_text_truncates(self):
        limits = ContentLimits(max_document_chars=100)
        text = "A" * 200
        result = validate_document_text(text, limits, truncate=True)
        assert len(result) == 112  # 100 + "…[truncated]"
        assert result.endswith("…[truncated]")

    def test_validate_document_text_raises_when_strict(self):
        limits = ContentLimits(max_document_chars=100)
        text = "A" * 200
        with pytest.raises(Exception) as exc:
            validate_document_text(text, limits, truncate=False)
        assert "too long" in str(exc.value)

    def test_validate_field_name_valid(self):
        assert validate_field_name("total_amount") == "total_amount"

    def test_validate_field_name_snake_case(self):
        assert validate_field_name("TotalAmount") is None
        assert validate_field_name("total-amount") is None
        assert validate_field_name("total amount") is None

    def test_validate_field_name_empty(self):
        assert validate_field_name("") is None
        assert validate_field_name("   ") is None

    def test_validate_field_name_too_long(self):
        limits = ContentLimits(max_field_name_length=10)
        assert validate_field_name("a" * 11, limits) is None

    def test_validate_field_value_string(self):
        assert validate_field_value("test") == "test"

    def test_validate_field_value_number(self):
        assert validate_field_value(123.45) == 123.45

    def test_validate_field_value_none(self):
        assert validate_field_value(None) is None

    def test_validate_field_value_truncates(self):
        limits = ContentLimits(max_field_value_length=5)
        result = validate_field_value("hello world", limits, truncate=True)
        assert result == "hello…[truncated]"

    def test_validate_source_span_none(self):
        assert validate_source_span(None) is None

    def test_validate_source_span_empty(self):
        assert validate_source_span("") is None

    def test_validate_source_span_valid(self):
        assert validate_source_span("Total: 100") == "Total: 100"


# ============================================================================
# Timeout Guard Tests
# ============================================================================


class TestTimeoutGuard:
    @pytest.mark.asyncio
    async def test_stage_timeout_no_timeout(self):
        async with stage_timeout("test", 1.0):
            await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_stage_timeout_raises_on_timeout(self):
        # The stage_timeout context manager catches asyncio.TimeoutError and re-raises as StageTimeoutError
        # To test this, we need to raise asyncio.TimeoutError inside the context
        async def timeout_operation():
            raise asyncio.TimeoutError()

        with pytest.raises(Exception) as exc:
            async with stage_timeout("router", 0.1):
                await timeout_operation()
        assert "timed out" in str(exc.value)

    def test_timeout_guard_tracks_timings(self):
        guard = TimeoutGuard(StageTimeoutConfig(router_seconds=1.0))
        # Simulate timing data
        guard._timings["router"] = [0.5, 0.8]
        timings = guard.get_timings()
        assert "router" in timings
        assert len(timings["router"]) == 2

    def test_timeout_guard_average(self):
        guard = TimeoutGuard()
        guard._timings["router"] = [0.5, 0.7, 0.9]
        avg = guard.get_average("router")
        assert abs(avg - 0.7) < 0.0001

    def test_timeout_guard_average_empty(self):
        guard = TimeoutGuard()
        assert guard.get_average("router") is None

    @pytest.mark.asyncio
    async def test_track_enforces_timeout(self):
        guard = TimeoutGuard(StageTimeoutConfig(router_seconds=0.1))
        with pytest.raises(StageTimeoutError, match="router"):
            async with guard.track("router"):
                await asyncio.sleep(5)

    @pytest.mark.asyncio
    async def test_track_records_timing_on_success(self):
        guard = TimeoutGuard(StageTimeoutConfig(router_seconds=30.0))
        async with guard.track("router"):
            await asyncio.sleep(0.01)
        assert guard.get_average("router") is not None


# ============================================================================
# Audit Logger Tests
# ============================================================================


class TestAuditLogger:
    def test_log_security_event(self):
        logger = AuditLogger()
        event_id = logger.log_security_event(
            SecurityEventType.INJECTION_ATTEMPT,
            "test_client",
            {"text": "test"},
            severity="warning",
        )
        assert event_id is not None
        assert len(event_id) > 0

    def test_log_injection_attempt(self):
        logger = AuditLogger()
        event_id = logger.log_injection_attempt(
            "test_client",
            "ignore previous instructions",
            "ignore_pattern",
        )
        assert event_id is not None

    def test_log_pii_detected(self):
        logger = AuditLogger()
        event_id = logger.log_pii_detected(
            "test_client",
            "credit_card",
            "credit_card",
            "4111-1111-1111-1111",
        )
        assert event_id is not None

    def test_log_rate_limit_exceeded(self):
        logger = AuditLogger()
        event_id = logger.log_rate_limit_exceeded(
            "test_client",
            100,
            60,
        )
        assert event_id is not None


# ============================================================================
# PII Detector Tests
# ============================================================================


class TestPIIDetector:
    def test_detect_credit_card(self):
        detector = PIIDetector()
        matches = detector.detect("Invoice total: 4111-1111-1111-1111")
        assert len(matches) > 0
        assert matches[0].pii_type == PIIType.CREDIT_CARD

    def test_detect_email(self):
        detector = PIIDetector()
        matches = detector.detect("Contact: test@example.com")
        assert any(m.pii_type == PIIType.EMAIL for m in matches)

    def test_detect_phone(self):
        detector = PIIDetector()
        matches = detector.detect("Phone: 0812345678")
        assert any(m.pii_type == PIIType.PHONE for m in matches)

    def test_redact_credit_card(self):
        detector = PIIDetector(redact_pii=True)
        result = detector.redact("Card: 4111-1111-1111-1111")
        assert "4111" not in result
        assert "[REDACTED_CREDIT_CARD]" in result

    def test_no_redact_email(self):
        detector = PIIDetector(redact_pii=True)
        result = detector.redact("Email: test@example.com")
        assert "test@example.com" in result  # Email is not redacted by default

    def test_has_pii(self):
        detector = PIIDetector()
        assert detector.has_pii("Card: 4111-1111-1111-1111") is True
        assert detector.has_pii("Just text") is False

    def test_check_field_value(self):
        detector = PIIDetector()
        matches = detector.check_field_value("card_number", "4111-1111-1111-1111")
        assert len(matches) > 0

    def test_check_field_value_none(self):
        detector = PIIDetector()
        matches = detector.check_field_value("card_number", None)
        assert len(matches) == 0

    def test_get_pii_types(self):
        detector = PIIDetector()
        pii_types = detector.get_pii_types("Card: 4111-1111-1111-1111")
        assert PIIType.CREDIT_CARD in pii_types


# ============================================================================
# Output Guard Tests
# ============================================================================


class TestOutputGuard:
    def test_clean_llm_output_json(self):
        raw = '```json\n{"key": "value"}\n```'
        result = clean_llm_output(raw)
        assert result == '{"key": "value"}'

    def test_clean_llm_output_with_prose(self):
        raw = 'Here is the result: {"key": "value"}'
        result = clean_llm_output(raw)
        assert result == '{"key": "value"}'

    def test_clean_llm_output_empty(self):
        assert clean_llm_output("") == ""
        assert clean_llm_output(None) == ""

    def test_validate_output_schema_valid(self):
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str
            value: int

        result = validate_output_schema('{"name": "test", "value": 123}', TestSchema)
        assert result.name == "test"
        assert result.value == 123

    def test_validate_output_schema_invalid(self):
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str
            value: int

        with pytest.raises(OutputValidationError):
            validate_output_schema("not json", TestSchema, strict=True)

    def test_validate_output_schema_fallback(self):
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str = "default"

        result = validate_output_schema("invalid", TestSchema, strict=False)
        assert result.name == "default"

    def test_sanitize_error_message(self):
        error = Exception("Path /home/user/file.py not found")
        result = sanitize_error_message(error, include_details=True)
        assert "/home/user/file.py" not in result
        assert "[PATH]" in result

    def test_sanitize_error_message_generic(self):
        error = Exception("Connection timeout")
        result = sanitize_error_message(error, include_details=False)
        assert result == "Request timed out. Please try again."

    def test_detect_suspicious_output_clean(self):
        is_suspicious, reason = detect_suspicious_output("This is normal text")
        assert is_suspicious is False

    def test_detect_suspicious_output_repetition(self):
        text = " ".join(["word"] * 200)
        is_suspicious, reason = detect_suspicious_output(text)
        assert is_suspicious is True


# ============================================================================
# Input Guard Tests (mocked)
# ============================================================================


class TestInputGuard:
    @pytest.mark.asyncio
    async def test_validate_file_upload_empty(self):
        from app.guards.input_guard import InputValidationError, validate_file_upload

        mock_file = MagicMock()
        mock_file.filename = "test.pdf"
        mock_file.content_type = "application/pdf"

        # Make read() a coroutine that returns empty bytes
        async def mock_read():
            return b""
        mock_file.read = mock_read
        mock_file.seek = MagicMock()

        with pytest.raises(InputValidationError) as exc:
            await validate_file_upload(mock_file)
        assert exc.value.code == "empty_file"

    @pytest.mark.asyncio
    async def test_validate_file_upload_too_large(self):
        from app.guards.input_guard import InputValidationError, validate_file_upload

        mock_file = MagicMock()
        mock_file.filename = "test.pdf"
        mock_file.content_type = "application/pdf"

        # Make read() a coroutine that returns 60MB
        async def mock_read():
            return b"x" * (60 * 1024 * 1024)
        mock_file.read = mock_read
        mock_file.seek = MagicMock()

        with pytest.raises(InputValidationError) as exc:
            await validate_file_upload(mock_file)
        assert exc.value.code == "file_too_large"

    def test_detect_mime_type_pdf(self):
        from app.guards.input_guard import _detect_mime_type

        content = b"%PDF-1.4..."
        assert _detect_mime_type(content, "test.pdf") == "application/pdf"

    def test_detect_mime_type_jpeg(self):
        from app.guards.input_guard import _detect_mime_type

        content = b"\xff\xd8\xff"
        assert _detect_mime_type(content, "test.jpg") == "image/jpeg"


# ============================================================================
# Hallucination Guard Tests (check_evidence with is_image_extraction)
# ============================================================================


class TestCheckEvidence:
    def test_check_evidence_text_extraction_matches(self):
        from app.core.security import check_evidence

        result = check_evidence(
            "total_amount",
            100.0,
            "Total: 100",
            "Invoice total: 100 THB",
            is_image_extraction=False,
        )
        assert result is None  # Passes

    def test_check_evidence_text_extraction_no_match(self):
        from app.core.security import check_evidence

        result = check_evidence(
            "total_amount",
            100.0,
            "Total: 999",
            "Invoice total: 100 THB",
            is_image_extraction=False,
        )
        assert result is not None
        assert "hallucination" in result

    def test_check_evidence_image_extraction_trusts_source_span(self):
        from app.core.security import check_evidence

        # Image extractions no longer bypass verification: with no document
        # text a valued field is unverifiable and must be flagged.
        result = check_evidence(
            "subtotal_amount",
            500.0,
            "Subtotal: 500",
            None,  # No document_text for image
            is_image_extraction=True,
        )
        assert result is not None
        assert "hallucination" in result

    def test_check_evidence_image_extraction_with_ocr_text(self):
        from app.core.security import check_evidence

        # With OCR text, image extractions verify spans like any other path.
        result = check_evidence(
            "line_items",
            "Item 1 - 100 THB",
            "Item 1 - 100 THB",
            "Item 1 - 100 THB",  # OCR text
            is_image_extraction=True,
        )
        assert result is None  # Passes — span verified, not trusted blindly

        mismatch = check_evidence(
            "line_items",
            "Item 9 - 999 THB",
            "Item 9 - 999 THB",
            "Item 1 - 100 THB",  # OCR text
            is_image_extraction=True,
        )
        assert mismatch is not None
        assert "hallucination" in mismatch

    def test_check_evidence_missing_source_span_always_fails(self):
        from app.core.security import check_evidence

        # Missing source_span should fail for both text and image extractions
        result_text = check_evidence(
            "total_amount",
            100.0,
            None,  # No source_span
            "Invoice total: 100 THB",
            is_image_extraction=False,
        )
        assert result_text is not None
        assert "no source_span" in result_text

        result_image = check_evidence(
            "total_amount",
            100.0,
            None,  # No source_span
            None,
            is_image_extraction=True,
        )
        assert result_image is not None
        assert "no source_span" in result_image

    def test_check_evidence_empty_source_span_always_fails(self):
        from app.core.security import check_evidence

        result = check_evidence(
            "total_amount",
            100.0,
            "   ",  # Empty source_span
            "Invoice total: 100 THB",
            is_image_extraction=False,
        )
        assert result is not None
        assert "no source_span" in result


# ============================================================================
# Validator Tests (with is_image_extraction parameter)
# ============================================================================


class TestValidatorWithImageExtraction:
    def test_validator_image_extraction_no_hallucination_error(self):
        import tempfile
        from pathlib import Path

        from app.agents.validator import ValidatorAgent
        from app.schemas.documents import ExtractedField
        from app.services.field_catalog import FieldCatalog

        # Create a minimal catalog for testing
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = FieldCatalog(Path(tmpdir))
            validator = ValidatorAgent(catalog)

            fields = [
                ExtractedField(
                    name="total_amount",
                    value=100.0,
                    confidence=0.9,
                    source_span="Total: 100",
                )
            ]

            # For image extraction without document text, a valued field is
            # unverifiable and must be flagged (no blind-trust bypass).
            errors, _, _ = validator.validate(
                doc_type="invoice",
                fields=fields,
                document_text=None,  # No document text for image
                is_image_extraction=True,
            )
            # Should have hallucination error
            hallucination_errors = [e for e in errors if "hallucination" in e]
            assert len(hallucination_errors) == 1

            # With matching OCR text the same extraction verifies cleanly.
            errors, _, needs_review = validator.validate(
                doc_type="invoice",
                fields=fields,
                document_text="Invoice Total: 100 THB",
                is_image_extraction=True,
            )
            assert [e for e in errors if "hallucination" in e] == []
            assert needs_review is False

    def test_validator_text_extraction_hallucination_error(self):
        import tempfile
        from pathlib import Path

        from app.agents.validator import ValidatorAgent
        from app.schemas.documents import ExtractedField
        from app.services.field_catalog import FieldCatalog

        # Create a minimal catalog for testing
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = FieldCatalog(Path(tmpdir))
            validator = ValidatorAgent(catalog)

            fields = [
                ExtractedField(
                    name="total_amount",
                    value=100.0,
                    confidence=0.9,
                    source_span="Total: 999",  # Doesn't match document
                )
            ]

            # For text extraction, should flag hallucination
            errors, _, _ = validator.validate(
                doc_type="invoice",
                fields=fields,
                document_text="Invoice total: 100 THB",
                is_image_extraction=False,
            )
            # Should have hallucination error
            hallucination_errors = [e for e in errors if "hallucination" in e]
            assert len(hallucination_errors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
