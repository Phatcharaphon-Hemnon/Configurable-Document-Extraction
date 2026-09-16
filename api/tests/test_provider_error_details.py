"""Tests for redacted provider error extraction and propagation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from openai import BadRequestError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _API_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.schemas.documents import ExtractionResult  # noqa: E402
from app.services.client import (  # noqa: E402
    Client,
    ClientError,
    _redact_provider_text,
    extract_provider_error,
)
from app.services.extraction_service import _provider_error_details  # noqa: E402


def _bad_request(message="Model not found: foo", *, request_id="req_test123"):
    req = httpx.Request("POST", "https://opencode.ai/zen/v1/chat/completions")
    resp = httpx.Response(
        400,
        headers={"x-request-id": request_id},
        request=req,
        json={"error": {"message": message, "code": "model_not_found",
                        "param": "model", "type": "invalid_request_error"}},
    )
    return BadRequestError(
        "short message",
        response=resp,
        body={"error": {"message": message, "code": "model_not_found",
                        "param": "model", "type": "invalid_request_error"}},
    )


def test_extract_nested_body_shape():
    details = extract_provider_error(_bad_request())
    assert details["status"] == 400
    assert details["code"] == "model_not_found"
    assert details["param"] == "model"
    assert details["request_id"] == "req_test123"
    assert details["message"] == "Model not found: foo"
    assert details["error_type"] == "BadRequestError"


def test_extract_flat_body_shape():
    req = httpx.Request("POST", "https://example/v1/chat/completions")
    resp = httpx.Response(400, request=req)
    err = BadRequestError("flat boom", response=resp, body={"message": "flat boom"})
    details = extract_provider_error(err)
    assert details["status"] == 400
    assert details["message"] == "flat boom"


def test_redaction_strips_secrets():
    text = _redact_provider_text("failed with api_key= sk-secret123 and Bearer abc.def.ghi")
    assert "sk-secret123" not in text
    assert "Bearer" not in text
    assert "[REDACTED]" in text


def test_provider_details_walk_chain():
    inner = ClientError("LLM API call failed (BadRequestError)",
                        provider_details={"status": 400, "message": "m", "error_type": "BadRequestError"})
    outer = RuntimeError("router blew up")
    outer.__cause__ = inner
    details = _provider_error_details(outer, stage="router", model="m1", provider="opencode")
    assert details is not None
    assert details.stage == "router"
    assert details.model == "m1"
    assert details.status == 400


def test_extraction_result_accepts_error_details():
    doc = ExtractionResult(
        doc_type="invoice",
        fields=[],
        validation_errors=["Router failed: x"],
        needs_review=True,
        completeness_score=0.0,
        error="Router failed: x",
        failed_stage="router",
        error_details={"stage": "router", "status": 400, "message": "Model not found"},
    )
    dumped = doc.model_dump()
    assert dumped["error_details"]["status"] == 400
    assert dumped["completeness_score"] == 0.0


@pytest.mark.anyio
async def test_request_mode_attaches_provider_details():
    from app.core.config import Settings

    settings = MagicMock(spec=Settings)
    settings.llm_api_key = "test-token"
    settings.llm_base_url = "https://opencode.ai/zen/v1"
    settings.llm_request_timeout_seconds = 90.0
    settings.disable_strict_json_schema = False
    client = Client(settings)

    async def mock_create(**kwargs):
        raise _bad_request("strict schema rejected here")

    client._client.chat.completions.create = mock_create
    with pytest.raises(ClientError) as exc_info:
        await client._request_mode({"model": "m", "messages": []}, {"model": "m"})
    details = exc_info.value.provider_details
    assert details["status"] == 400
    assert "strict schema rejected" in details["message"]
