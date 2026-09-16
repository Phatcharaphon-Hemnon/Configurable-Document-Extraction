"""Deterministic replay parity: identical OCR text + recorded model outputs.

Feeds a fixed synthetic page and fixture LLM payloads through the real
Router/Extractor agents + production validator/acceptance, then asserts the
accepted contract and dumps a NORMALIZED result (env REPLAY_DUMP path, else
tmp) for cross-tree comparison (main vs local branch vs perf).

Normalization drops only intentionally variable metadata: ids, timestamps,
source URLs, timings, usage, cache fingerprints. Model answers are never
required to match across live models — fixtures pin them here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parents[1]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.core.config import Settings  # noqa: E402
from app.schemas.llm_schemas import (  # noqa: E402
    ExtractionResponseSchema,
    JudgeResponseSchema,
    RoutingResponseSchema,
)
from app.services.client import ClientResult  # noqa: E402
from app.services.extraction_service import DocumentExtractionService  # noqa: E402

_FIX = Path(__file__).resolve().parent / "fixtures" / "replay"


def _load(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def _service(tmp_path: Path) -> DocumentExtractionService:
    shutil.copytree("api/app/data/knowledge_base", tmp_path / "kb",
                    ignore=shutil.ignore_patterns("documents"))
    s = Settings()
    s.knowledge_base_path = str(tmp_path / "kb")
    s.database_enabled = False
    s.source_storage_path = str(tmp_path / "sources")
    s.cache_path = str(tmp_path / "cache")
    s.ocr_cache_path = str(tmp_path / "cache" / "ocr-results")
    s.result_cache_enabled = False
    s.temporal_enabled = False
    return DocumentExtractionService(settings=s)


def _result(schema_cls, payload: dict):
    return ClientResult(
        parsed=schema_cls.model_validate_json(json.dumps(payload)),
        raw_text=json.dumps(payload), raw_response={"id": "replay"},
        request_summary={"model": "replay-fixture"},
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        diagnosis="ok", finish_reason="stop",
    )


def _normalize(doc) -> dict:
    data = doc.model_dump(mode="json")
    for key in ("id", "extracted_at", "source", "timings", "usage",
                "cache_metadata", "full_text", "ocr_blocks"):
        data.pop(key, None)
    if data.get("judge"):
        data["judge"].pop("notes", None)
    return data


@pytest.mark.asyncio
async def test_replay_pipeline_contract(tmp_path, monkeypatch):
    page_text = _load("page_text.txt")
    router_fixture = json.loads(_load("router.json"))
    extraction_fixture = json.loads(_load("extraction.json"))
    judge_fixture = json.loads(_load("judge.json"))

    async def fake_generate(self, **kwargs):
        name = kwargs["response_schema"].__name__
        if name == RoutingResponseSchema.__name__:
            return _result(RoutingResponseSchema, router_fixture)
        if name == ExtractionResponseSchema.__name__:
            return _result(ExtractionResponseSchema, extraction_fixture)
        if name == JudgeResponseSchema.__name__:
            return _result(JudgeResponseSchema, judge_fixture)
        raise AssertionError(f"unexpected schema {name}")

    monkeypatch.setattr(
        "app.services.client.Client.generate_structured", fake_generate)
    svc = _service(tmp_path)
    doc = await svc._extract_one_page(
        "replay.png", page_text, page_number=1, blocks=[], ocr_uncertain=False)

    # Accepted contract (real agents + production validator/acceptance).
    assert doc.doc_type == "invoice"
    names = {f.name for f in doc.fields}
    assert {"invoice_number", "invoice_date", "seller_name", "total_amount"} <= names
    assert doc.fields and all(f.source_span and f.source_span in page_text for f in doc.fields)
    assert len(doc.tables) == 1 and doc.tables[0].name == "line_items"
    assert sum(len(r) for t in doc.tables for r in t.rows) == 8
    assert doc.failed_stage is None and doc.error is None
    assert doc.judge_status in ("passed", "skipped")
    assert doc.completeness_score == 1.0
    assert doc.needs_review is False

    dump = Path(os.environ.get("REPLAY_DUMP", str(tmp_path / "replay.normalized.json")))
    dump.write_text(json.dumps(_normalize(doc), sort_keys=True, indent=1), encoding="utf-8")
