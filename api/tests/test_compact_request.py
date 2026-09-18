"""Compact extraction requests (opt-in): reduced prompt-eval work, same contract.

RED-first regressions for EXTRACTION_COMPACT_REQUEST (default off):
- Default behavior is byte-identical (full JSON-schema text suffix kept).
- Compact mode drops the redundant prompt-text schema dump only on the
  json_schema tier (response_format.json_schema carries the full effective
  schema), while keeping the short output-contract paragraph (table-
  inside-tables rule) and the full wire response_format.
- json_object (bare {"type": "json_object"}) and plain (no response_format)
  keep the full suffix: the prompt text is their only schema contract.
- Tier fallback to json_object/plain rebuilds messages with the full suffix.
- Corrective instruction references the enforced schema only on json_schema;
  on json_object/plain it references "the Schema above".
- Deterministic replay: identical canned output parses to identical
  fields/tables/evidence in both modes (mock parity of construction only,
  never a model-quality claim).
- Fingerprints (result cache + region model) differ by flag so old cached
  results cannot masquerade as compact-mode results.

No live inference; mocked transport only. No gold input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.schemas.llm_schemas import ExtractionResponseSchema  # noqa: E402
from app.services.client import (  # noqa: E402
    Client,
    _build_json_prompt_suffix,
    build_structured_messages,
    compact_request_enabled,
)


@pytest.fixture(autouse=True)
def _isolate_capability_memory():
    """Capability memory is process-global: reset per test for order safety."""
    Client.reset_capability_memory()
    yield
    Client.reset_capability_memory()


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
    resp.model_dump = MagicMock(
        return_value={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    )
    return resp


def _client(payloads, *, compact: bool = False):
    settings = SimpleNamespace(
        llm_api_key="t",
        llm_base_url="https://compact-test.invalid/v1",
        llm_request_timeout_seconds=5.0,
        llm_max_concurrent_requests=1,
        llm_provider="test",
        llm_temperature=0.0,
        disable_strict_json_schema=False,
        llm_reasoning_effort="",
        extraction_compact_request=compact,
    )
    c = Client(settings)

    async def _create(**kwargs):
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    c._client.chat.completions.create = _create
    calls: list[dict] = []
    orig = c._client.chat.completions.create

    async def counting(**kwargs):
        calls.append(kwargs)
        return await orig(**kwargs)

    c._client.chat.completions.create = counting
    return c, calls


def _valid_payload() -> str:
    return json.dumps({
        "fields": [{"name": "total_amount", "value": "60.00",
                    "confidence": 0.9, "source_span": "TOTAL 60.00"}],
        "tables": [{"name": "line_items",
                    "columns": [{"key": "product_name", "label": "Item"}],
                    "rows": [[{"column": "product_name", "value": "Tea",
                               "confidence": 0.9, "source_span": "Tea"}]]}],
    })


def test_compact_flag_defaults_off():
    assert compact_request_enabled(SimpleNamespace()) is False
    assert compact_request_enabled(SimpleNamespace(extraction_compact_request=False)) is False
    assert compact_request_enabled(SimpleNamespace(extraction_compact_request=True)) is True
    # MagicMock settings (legacy doubles) must NOT enable compact implicitly.
    assert compact_request_enabled(MagicMock()) is False


def test_default_messages_keep_full_schema_dump():
    full = _build_json_prompt_suffix(ExtractionResponseSchema)
    assert '"fields"' in full and "Schema:" in full
    msgs = build_structured_messages(
        prompt="P", response_schema=ExtractionResponseSchema,
        compact=False, tier="json_schema")
    assert msgs == [{"role": "user", "content": "P" + full}]


def test_compact_messages_drop_dump_keep_contract_on_json_schema_tier():
    msgs = build_structured_messages(
        prompt="P", response_schema=ExtractionResponseSchema,
        compact=True, tier="json_schema")
    body = msgs[0]["content"]
    assert body.startswith("P")
    # Redundant raw schema dump is gone...
    assert "Schema:" not in body
    assert '"additionalProperties"' not in body
    # ...but the proven output-contract paragraph stays.
    assert "never as the root" in body
    assert '"fields"' in body and '"tables"' in body
    assert len(body) < len("P" + _build_json_prompt_suffix(ExtractionResponseSchema)) - 2000


def test_compact_messages_keep_full_suffix_without_structural_schema():
    # json_object sends only {"type": "json_object"} (no schema) and plain
    # sends no response_format at all: the prompt text is their only schema
    # contract, so compact mode must keep the full dump there.
    full = _build_json_prompt_suffix(ExtractionResponseSchema)
    for tier in ("json_object", "plain"):
        msgs = build_structured_messages(
            prompt="P", response_schema=ExtractionResponseSchema,
            compact=True, tier=tier)
        assert msgs[0]["content"] == "P" + full


@pytest.mark.anyio
async def test_compact_request_keeps_wire_schema_and_parses_identically():
    for compact in (False, True):
        c, calls = _client([_resp(_valid_payload())], compact=compact)
        r = await c.generate_structured(
            model="m", prompt="P", response_schema=ExtractionResponseSchema)
        assert r.parsed.fields[0].name == "total_amount"
        assert r.parsed.fields[0].source_span == "TOTAL 60.00"
        assert r.parsed.tables[0].name == "line_items"
        # Wire contract unchanged in both modes.
        fmt = calls[0]["response_format"]
        assert fmt["type"] == "json_schema"
        assert set(fmt["json_schema"]["schema"]["properties"]) >= {"fields", "tables"}
        body = calls[0]["messages"][0]["content"]
        if compact:
            assert "Schema:" not in body
            assert "never as the root" in body
        else:
            assert "Schema:" in body


@pytest.mark.anyio
async def test_compact_corrective_references_enforced_schema():
    bad = {"fields": [{"name": "total_amount", "value": "60", "confidence": 0.9}], "tables": []}
    c, calls = _client([_resp(json.dumps(bad)), _resp(_valid_payload())], compact=True)
    r = await c.generate_structured(
        model="m", prompt="P", response_schema=ExtractionResponseSchema)
    assert r.parsed.fields[0].source_span == "TOTAL 60.00"
    assert len(calls) == 2
    # No dangling "Schema above" pointer: the schema is enforced, not printed.
    assert "Schema above" not in str(calls[1])
    assert "enforced response schema" in str(calls[1])


@pytest.mark.anyio
async def test_compact_fallback_to_json_object_restores_full_suffix():
    # json_schema explicitly rejected by the provider → fallback to
    # json_object must rebuild with the FULL suffix (json_object carries no
    # schema; prompt text is the only contract). Exactly one contract copy.
    rejection = Exception("response_format json_schema unsupported by model")
    rejection.status_code = 400  # type: ignore[attr-defined]
    c, calls = _client([rejection, _resp(_valid_payload())], compact=True)
    c._strongest_tier = MagicMock(return_value="json_schema")  # type: ignore[method-assign]
    r = await c.generate_structured(
        model="m", prompt="P", response_schema=ExtractionResponseSchema)
    assert r.parsed.fields[0].name == "total_amount"
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert "Schema:" not in calls[0]["messages"][0]["content"]
    fallback_body = calls[1]["messages"][0]["content"]
    assert "Schema:" in fallback_body
    assert fallback_body.count("Schema:") == 1


@pytest.mark.anyio
async def test_compact_corrective_on_json_object_references_schema_above():
    # Starting tier json_object (full suffix present): the corrective must
    # reference "the Schema above", never the enforced schema.
    bad = {"fields": [{"name": "total_amount", "value": "60", "confidence": 0.9}], "tables": []}
    c, calls = _client([_resp(json.dumps(bad)), _resp(_valid_payload())], compact=True)
    c._strongest_tier = MagicMock(return_value="json_object")  # type: ignore[method-assign]
    r = await c.generate_structured(
        model="m", prompt="P", response_schema=ExtractionResponseSchema)
    assert r.parsed.fields[0].source_span == "TOTAL 60.00"
    assert len(calls) == 2
    assert "Schema:" in calls[0]["messages"][0]["content"]
    assert "the Schema above" in str(calls[1])
    assert "enforced response schema" not in str(calls[1])


def test_fingerprints_differ_by_compact_flag():
    from app.services.result_cache import ResultCache

    off = SimpleNamespace(
        cache_path="/tmp/compact-fp-test", result_cache_ttl_seconds=60,
        result_cache_max_entries=8, result_cache_enabled=True,
        ocr_engine="tesseract", ocr_languages="eng+tha", ocr_dpi=300,
        llm_base_url="https://test.invalid/v1", router_model_name="m",
        extraction_model_name="m", judge_model_name="m", llm_temperature=0.0,
        extraction_max_tokens=3000, router_max_tokens=400, router_text_chars=2000,
        disable_strict_json_schema=False, few_shot_examples_per_doc_type=0,
        llm_reasoning_effort="", judge_skip_when_clean=True,
        judge_skip_confidence=0.85, knowledge_base_path="/tmp/compact-fp-kb",
        extraction_compact_request=False,
    )
    on = SimpleNamespace(**{**vars(off), "extraction_compact_request": True})
    fp_off = ResultCache(off).config_fingerprint_dict()
    fp_on = ResultCache(on).config_fingerprint_dict()
    assert fp_off != fp_on
    assert fp_off["provider"]["compact_request"] is False
    assert fp_on["provider"]["compact_request"] is True
