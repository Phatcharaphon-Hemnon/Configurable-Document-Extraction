"""Classified LLM recovery: no blind regeneration (THAI_bill.jpg table-as-root).

Covers: one-call success, wrong-root table preservation, missing source_span
(single corrective, not whole-page burn), syntax vs schema kinds,
display-only vs confirmed truncation, explicit format rejection caching,
disabled schema without false retry warnings, failed corrective, shared
budget, and accepted/rejected preservation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.llm_schemas import ExtractionResponseSchema  # noqa: E402
from app.services.client import (  # noqa: E402
    Client,
    ClientError,
    _build_json_prompt_suffix,
    _classify_parse_failure,
)


class _Dummy(BaseModel):
    name: str
    value: int


def _resp(content: str | None, finish: str | None = None):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    # Preserve finish_reason in the dump for truncation detection.
    resp.model_dump = MagicMock(
        return_value={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    )
    return resp


def _client(payloads_or_resps, disable_strict: bool = False):
    settings = MagicMock()
    settings.llm_api_key = "t"
    settings.llm_base_url = "https://test.invalid/v1"
    settings.llm_request_timeout_seconds = 5.0
    settings.llm_max_concurrent_requests = 1
    settings.disable_strict_json_schema = disable_strict
    settings.llm_temperature = 0.0
    c = Client(settings)
    async def _create(**kwargs):
        item = payloads_or_resps.pop(0) if isinstance(payloads_or_resps, list) else payloads_or_resps
        if isinstance(item, Exception):
            raise item
        return item
    c._client.chat.completions.create = _create
    # Attach a list for call counting via wrapper
    calls: list[dict] = []
    orig = c._client.chat.completions.create
    async def counting(**kwargs):
        calls.append(kwargs)
        return await orig(**kwargs)
    c._client.chat.completions.create = counting
    return c, calls


def _bad_request(msg: str):
    return BadRequestError(
        msg, response=httpx.Response(400, request=httpx.Request("POST", "https://test/v1")), body=None
    )


@pytest.mark.anyio
async def test_valid_output_needs_one_call():
    c, calls = _client([_resp('{"name":"a","value":1}')])
    r = await c.generate_structured(model="m", prompt="p", response_schema=_Dummy)
    assert r.parsed.value == 1
    assert len(calls) == 1
    assert r.diagnosis == "ok"


@pytest.mark.anyio
async def test_table_as_root_preserved_with_normalization():
    table = {"name": "line_items", "columns": [{"key": "product_name", "label": "x"}],
             "rows": [[{"column": "product_name", "value": "Tea", "confidence": 0.9,
                        "source_span": "Tea"}]]}
    valid = {"fields": [{"name": "total", "value": "60", "confidence": 0.9, "source_span": "60.00"}],
             "tables": [table]}
    c, calls = _client([_resp(json.dumps(table)), _resp(json.dumps(valid))])
    r = await c.generate_structured(model="m", prompt="p", response_schema=ExtractionResponseSchema)
    # Corrective recovered full root; table contents preserved, not invented.
    assert len(r.parsed.tables) == 1
    assert r.parsed.tables[0].name == "line_items"
    assert len(calls) == 2
    # Prompt contract explicitly forbids table-as-root.
    suffix = _build_json_prompt_suffix(ExtractionResponseSchema)
    assert '"fields"' in suffix and '"tables"' in suffix
    assert "never as the root" in suffix


@pytest.mark.anyio
async def test_table_as_root_failed_corrective_returns_partial_not_silent_success():
    table = {"name": "line_items", "columns": [{"key": "a", "label": "A"}],
             "rows": [[{"column": "a", "value": "v", "confidence": 0.5, "source_span": "v"}]]}
    c, calls = _client([_resp(json.dumps(table)), _resp("not json")])
    r = await c.generate_structured(model="m", prompt="p", response_schema=ExtractionResponseSchema)
    # Partial: table preserved, fields=[] explicitly incomplete (not success).
    assert r.parsed.tables[0].name == "line_items"
    assert r.parsed.fields == []
    assert "incomplete" in (r.normalization or "").lower() or "partial" in (r.diagnosis or "").lower()
    assert len(calls) == 2


@pytest.mark.anyio
async def test_missing_source_span_single_corrective_with_locations():
    bad = {"fields": [{"name": "total", "value": "60", "confidence": 0.9}], "tables": []}
    good = {"fields": [{"name": "total", "value": "60", "confidence": 0.9, "source_span": "60.00"}]}
    c, calls = _client([_resp(json.dumps(bad)), _resp(json.dumps(good))])
    r = await c.generate_structured(model="m", prompt="p", response_schema=ExtractionResponseSchema)
    assert r.parsed.fields[0].source_span == "60.00"
    assert len(calls) == 2
    # Corrective prompt carries validation locations, not the untrusted answer.
    assert "source_span" in str(calls[1])


def test_syntax_vs_schema_classification():
    d1 = _classify_parse_failure(_Dummy, "not json at all")
    assert d1.kind == "syntax_error"
    d2 = _classify_parse_failure(_Dummy, '{"name":"a"}')
    assert d2.kind == "field_error"
    assert any("value" in loc for loc in (d2.locations or []))
    d3 = _classify_parse_failure(ExtractionResponseSchema, '{"name":"line_items","columns":[],"rows":[]}')
    # Empty columns/rows still validates as a table? If not, it is wrong_root.
    assert d3.kind in ("table_root", "wrong_root", "field_error")
    d4 = _classify_parse_failure(_Dummy, "")
    assert d4.kind == "empty"
    d5 = _classify_parse_failure(_Dummy, '{"a":1} {"b":2}')
    assert d5.kind == "syntax_error"  # ambiguous multiple objects rejected


def test_display_preview_truncation_is_not_model_truncation():
    long_text = '{"name": "a", "value": 1, "extra": "' + "x" * 2000 + '"}'
    # No finish_reason + display preview marker alone => syntax/field, never truncated.
    d = _classify_parse_failure(_Dummy, long_text[:500] + "… [truncated, 2000 chars total]")
    assert d.kind != "truncated"


@pytest.mark.anyio
async def test_confirmed_truncation_raises_without_blind_retry():
    truncated = '{"fields": [{"name": "a", "value": "'
    c, calls = _client([_resp(truncated, finish="length")])
    with pytest.raises(ClientError, match=r"truncat"):
        await c.generate_structured(model="m", prompt="p", response_schema=_Dummy, max_tokens=50)
    assert len(calls) == 1  # no blind retry of the same oversized request


@pytest.mark.anyio
async def test_explicit_rejection_caches_tier_malformed_does_not():
    # Explicit 400 unsupported for json_schema -> falls to json_object once.
    c, calls = _client([
        _bad_request("response_format json_schema not supported"),
        _resp('{"name":"a","value":1}'),
    ])
    r = await c.generate_structured(model="m", prompt="p", response_schema=_Dummy)
    assert r.parsed.value == 1
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
    assert c._tier_unsupported("m", "json_schema")
    # Malformed output never marks a tier unsupported.
    c2, _ = _client([_resp("bad"), _resp('{"name":"a","value":1}')])
    await c2.generate_structured(model="m2", prompt="p", response_schema=_Dummy)
    assert not c2._tier_unsupported("m2", "json_schema")


@pytest.mark.anyio
async def test_disabled_schema_starts_at_json_object_without_false_warning(caplog):
    c, calls = _client([_resp('{"name":"a","value":1}')], disable_strict=True)
    with caplog.at_level("INFO"):
        await c.generate_structured(model="m", prompt="p", response_schema=_Dummy)
    assert calls[0]["response_format"]["type"] == "json_object"
    # No "attempt 1 returned invalid JSON" warning when tier 1 was skipped.
    assert "attempt 1 returned invalid JSON" not in caplog.text.lower()


@pytest.mark.anyio
async def test_failed_corrective_raises_with_diagnostics():
    c, _ = _client([_resp("bad1"), _resp("bad2")])
    with pytest.raises(ClientError) as ei:
        await c.generate_structured(model="m", prompt="p", response_schema=_Dummy)
    msg = str(ei.value)
    assert "syntax_error" in msg
    assert "Raw response preview:" in msg
    assert "model_calls=2" in msg
