#!/usr/bin/env python3
"""Phase 5 follow-up (PREPARED — DO NOT RUN without explicit authorization).

Single-dispatch STREAMING Extractor diagnostic for the failed local page:
  sroie_X51008142033.jpg / job 334f03b7-de2e-49d7-9c18-79ea243926cc

Purpose: observability only — first-token latency, inter-token timing, and
time-correlated resource sampling that a non-streaming timeout cannot
provide. Streaming does NOT accelerate generation (same server compute),
and closing the client is NOT assumed to stop server computation.

Request parity with the non-streaming attempt (unavoidable differences
recorded, never hidden):
  same endpoint / model / prompt / schema / temperature / max_tokens /
  disable_reasoning. Differences: stream=true; no stream_options (usage
  stays unavailable by design); incremental vs whole-response delivery.
  response_format=json_schema is preserved; if the endpoint rejects the
  combination the run stops and reports the limitation (never silently
  drops the format).

Bounds (diagnostic-only, process-local; production defaults untouched):
  read-idle timeout 45s (no event, not just no content); generation-stage
  wall-clock deadline 150s (generation cannot continue beyond it);
  overall script deadline 180s including preflight and cleanup.

Exactly one HTTP dispatch (second-dispatch guard); no Router/Judge, no
retries, no tier fallbacks, no corrective generation. Partial JSON,
truncation (finish_reason=length), cancellation, and errors are classified
with the existing contracts; a parsed response alone never proves
extraction accuracy. Judge unavailable; cache-ineligible by construction.
Isolated storage only (temp dirs, /tmp report). No History/sources/cache
writes, no downloads, no model/provider changes, no pushes.

Usage (operator, from repo root, venv active):
  python api/scripts/diag_extractor_stream.py [--job-id ...] [--out ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import diag_extractor_single as single  # noqa: E402 (provenance gate reuse)

READ_IDLE_S = 45.0
STAGE_DEADLINE_S = 150.0
OVERALL_DEADLINE_S = 180.0
SAMPLE_EVERY_S = 5.0


async def _sample_resources(stop: asyncio.Event, out: list) -> None:
    while not stop.is_set():
        out.append({"at": round(time.perf_counter(), 3),
                    "mem": single._meminfo()})
        try:
            await asyncio.wait_for(stop.wait(), timeout=SAMPLE_EVERY_S)
        except asyncio.TimeoutError:
            continue


async def _run_stream(args) -> dict:
    from openai import AsyncOpenAI

    from app.agents.extractors import _build_prompt
    from app.core.config import Settings
    from app.schemas.llm_schemas import ExtractionResponseSchema
    from app.services.client import (
        Client,
        _pydantic_to_json_schema,
        _resolve_temperature,
        build_structured_messages,
        compact_request_enabled,
    )
    from app.services.field_catalog import FieldCatalog
    from app.services.provider_capabilities import (
        build_chat_kwargs,
        resolve_capabilities,
    )
    from app.services.request_control import collect_dispatches
    from app.services.streaming_diag import (
        StreamIdleTimeout,
        StreamOverallTimeout,
        collect_stream_chunks,
        remaining_overall,
        single_dispatch_guard,
    )

    report: dict = {"job_id": args.job_id, "status": "started",
                    "judge": "unavailable (by design)",
                    "cache_eligible": False, "stream": True}
    try:
        prov = single.verify_provenance(
            Path(args.db), Path(args.sources), args.job_id)
    except RuntimeError as exc:
        return {**report, "status": "blocked", "block_reason": str(exc)}
    text = prov.pop("text")
    report["provenance"] = prov

    settings = Settings()  # effective deployment config; secrets never printed
    model = settings.extraction_model_name
    temperature = _resolve_temperature(settings, None)
    tmp = Path(tempfile.mkdtemp(prefix="diag_stream_"))
    try:
        live_catalog = Path(settings.knowledge_base_path) / "field_catalog"
        shutil.copytree(live_catalog, tmp / "field_catalog")
        catalog = FieldCatalog(tmp)
        # Identical prompt to the non-streaming attempt.
        prompt = _build_prompt(
            "invoice", catalog.compact_for_prompt("invoice"),
            __import__("app.core.security", fromlist=["sanitize_document_text"])
            .sanitize_document_text(text), None)
        schema_json = _pydantic_to_json_schema(ExtractionResponseSchema)
        profile = resolve_capabilities(
            endpoint=settings.llm_base_url, model=model,
            provider_label=str(settings.llm_provider or ""))
        compact = compact_request_enabled(settings)
        kwargs = build_chat_kwargs(
            model=model,
            messages=build_structured_messages(
                prompt=prompt, response_schema=ExtractionResponseSchema,
                compact=compact, tier="json_schema"),
            temperature=temperature,
            token_param=profile.token_param,
            max_tokens=settings.extraction_max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": "ExtractionResponseSchema", "strict": True,
                "schema": schema_json}},
            reasoning_effort="",
            disable_reasoning=True,
            omit_extra_body_reasoning=profile.omit_extra_body_reasoning,
            stream=True,
        )
        report["settings"] = {
            "provider": settings.llm_provider,
            "endpoint": settings.llm_base_url,
            "model": model,
            "temperature": temperature,
            "extraction_max_tokens": settings.extraction_max_tokens,
            "request_timeout_s": settings.llm_request_timeout_seconds,
            "read_idle_s": READ_IDLE_S,
            "stage_deadline_s": STAGE_DEADLINE_S,
            "overall_deadline_s": OVERALL_DEADLINE_S,
            "region_extraction_enabled": settings.region_extraction_enabled,
            "compact_request": compact,
            "prompt_chars": len(prompt),
            "message_chars": len(kwargs["messages"][0]["content"]),
            "schema_chars": len(json.dumps(schema_json)),
        }
        report["request_differences"] = [
            "stream=true (incremental delivery; same server compute)",
            "no stream_options (usage unavailable by design)",
            "SDK timeout 180s (connect/headers/reads; longer than the 150s "
            "stage, so the stage guard fires first)",
        ]
        report["model_state_before"] = single._ollama_ps(settings.llm_base_url)
        report["memory_before"] = single._meminfo()

        api_key = settings.llm_api_key or "dummy-key"
        sdk = AsyncOpenAI(api_key=api_key, base_url=settings.llm_base_url,
                          timeout=OVERALL_DEADLINE_S, max_retries=0)
        guarded = single_dispatch_guard(sdk.chat.completions.create)
        samples: list = []
        stop = asyncio.Event()
        sampler = asyncio.ensure_future(_sample_resources(stop, samples))
        outcome: dict = {}
        started = time.perf_counter()
        # established_at is the SDK create-return moment (stream
        # establishment, NOT a header timestamp — the SDK exposes no
        # header hook). It separates a create-stall (None at deadline)
        # from a mid-stream stall (set, zero events). The inner overall
        # budget is the REMAINDER of the stage (a full second inner
        # deadline from here could never fire before the outer stage).
        established_at: float | None = None
        try:
            try:
                async with asyncio.timeout(STAGE_DEADLINE_S):
                    stream = await guarded(**kwargs)
                    established_at = time.perf_counter()
                    with collect_dispatches():
                        result = await collect_stream_chunks(
                            stream, idle_timeout=READ_IDLE_S,
                            overall_deadline=remaining_overall(
                                STAGE_DEADLINE_S, started),
                            established_at=established_at)
                duration = time.perf_counter() - started
                outcome = {"status": "stream-completed",
                           "duration_s": round(duration, 3)}
            except (StreamIdleTimeout, StreamOverallTimeout) as exc:
                duration = time.perf_counter() - started
                outcome = {"status": "stream-incomplete",
                           "kind": type(exc).__name__,
                           "duration_s": round(duration, 3),
                           "partial_chars": len(exc.partial_text),
                           "partial_chunks": exc.chunk_count}
                result = None
                result_text = exc.partial_text
            else:
                result_text = result.text
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 (format rejection etc.)
            duration = time.perf_counter() - started
            outcome = {"status": "dispatch-failed",
                       "duration_s": round(duration, 3),
                       "error": f"{type(exc).__name__}: {str(exc)[:500]}"}
            result = None
            result_text = ""
        finally:
            stop.set()
            sampler.cancel()
            try:
                await sampler
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            try:
                await sdk.close()
            except Exception:  # noqa: BLE001
                pass  # closing the client is not assumed to stop the server
        # Content classification (lengths + kinds only; never log the text).
        parsed, diagnosis = Client._try_parse_detailed(
            ExtractionResponseSchema, result_text or "")
        diag_kind = diagnosis.kind if diagnosis else "ok"
        if result is not None and result.finish_reason == "length":
            diag_kind = f"length-incomplete({diag_kind})"
        ts = result.timestamps if result is not None else None
        outcome.update({
            "assembled_chars": len(result_text or ""),
            "chunk_count": result.chunk_count if result is not None else 0,
            "empty_count": result.empty_count if result is not None else 0,
            "finish_reason": result.finish_reason if result is not None else None,
            "parsed": parsed is not None,
            "diagnosis": diag_kind,
            "established_at_s": (
                round(established_at - started, 3)
                if established_at is not None else None),
            "timestamps": {
                "dispatched_at": ts.dispatched_at if ts else None,
                "established_at": ts.established_at if ts else established_at,
                "first_event_at": ts.first_event_at if ts else None,
                "first_content_at": ts.first_content_at if ts else None,
                "completed_at": ts.completed_at if ts else None,
            } if (ts or established_at is not None) else None,
            "inter_arrival": {
                "count": len(result.inter_arrival_s) if result else 0,
                "max_s": round(max(result.inter_arrival_s), 3)
                if result and result.inter_arrival_s else None,
            },
            "usage": "unavailable (no stream_options by design)",
        })
        report.update(outcome)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["model_state_after"] = single._ollama_ps(settings.llm_base_url)
    report["memory_after"] = single._meminfo()
    report["resource_samples"] = samples
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single-dispatch streaming Extractor diagnostic (prepared, gated).")
    parser.add_argument("--job-id", default=single.DEFAULT_JOB)
    parser.add_argument("--db", default=str(REPO_ROOT / "data-local" / "extraction.db"))
    parser.add_argument("--sources", default=str(REPO_ROOT / "data-local" / "sources"))
    parser.add_argument("--out", default="/tmp/diag_extractor_stream.json")
    args = parser.parse_args()
    try:
        report = await asyncio.wait_for(_run_stream(args), timeout=OVERALL_DEADLINE_S)
    except asyncio.TimeoutError:
        report = {"job_id": args.job_id, "status": "overall-deadline-expired",
                  "judge": "unavailable (by design)", "cache_eligible": False}
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "stream-completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
