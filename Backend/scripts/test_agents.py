#!/usr/bin/env python3
"""Run each agent against a real document image and print API request/response proof."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from Backend.app.agents.extractors import ExtractionContext, ExtractorFactory  # noqa: E402
from Backend.app.agents.judge import JudgeAgent  # noqa: E402
from Backend.app.agents.router import RouterAgent  # noqa: E402
from Backend.app.core.config import get_settings  # noqa: E402
from Backend.app.schemas.documents import DocumentType  # noqa: E402
from Backend.app.services.gemini_client import GeminiCallError, GeminiClient  # noqa: E402


def _load_image(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    mime_type, _ = mimetypes.guess_type(str(path))
    return data, mime_type or "image/png"


def _print_section(title: str, payload: object) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)
    print(json.dumps(payload, indent=2, default=str))


def run_router(image_path: Path, settings) -> None:
    image_bytes, mime_type = _load_image(image_path)
    client = GeminiClient(settings)
    agent = RouterAgent(settings)

    from Backend.app.schemas.gemini_schemas import RoutingResponseSchema

    prompt = (
        "You are a document classification router.\n"
        "Classify this document into exactly one of: invoice, po, delivery_note.\n"
        "Also detect the primary language: en or th.\n"
        "Base your decision on the actual document content — not the filename."
    )
    result = client.generate_structured(
        model=settings.router_model_name,
        prompt=prompt,
        response_schema=RoutingResponseSchema,
        image_bytes=image_bytes,
        image_mime_type=mime_type,
    )
    _print_section("ROUTER — request payload (truncated)", result.request_summary)
    _print_section("ROUTER — raw API response", result.raw_response)
    decision = agent.classify(
        filename=image_path.name,
        content_type=mime_type,
        image_bytes=image_bytes,
        image_mime_type=mime_type,
    )
    _print_section("ROUTER — parsed RoutingDecision", decision.__dict__)


def run_extractor(image_path: Path, doc_type: DocumentType, settings) -> None:
    image_bytes, mime_type = _load_image(image_path)
    factory = ExtractorFactory(settings)
    extractor = factory.create(doc_type)
    context = ExtractionContext(
        text="",
        metadata={"filename": image_path.name},
        image_bytes=image_bytes,
        image_mime_type=mime_type,
    )
    fields = extractor.extract(context)
    _print_section(
        "EXTRACTOR — parsed fields",
        {name: field.model_dump() for name, field in fields.items()},
    )


def run_judge(image_path: Path, prediction: dict, settings) -> None:
    image_bytes, mime_type = _load_image(image_path)
    agent = JudgeAgent(settings)
    result = agent.evaluate(
        prediction=prediction,
        source_text=None,
        image_bytes=image_bytes,
        image_mime_type=mime_type,
    )
    _print_section("JUDGE — parsed JudgeResult", result.model_dump())


def main() -> int:
    parser = argparse.ArgumentParser(description="Test real Gemini agent integration")
    parser.add_argument("image", type=Path, help="Path to a real document image")
    parser.add_argument(
        "--doc-type",
        choices=["invoice", "po", "delivery_note"],
        default="invoice",
        help="Document type for extractor test (default: invoice)",
    )
    args = parser.parse_args()

    if not args.image.is_file():
        print(f"Error: image file not found: {args.image}", file=sys.stderr)
        return 1

    settings = get_settings()
    if not settings.gemini_api_key.strip():
        print("Error: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        return 1

    doc_type = DocumentType(args.doc_type)

    try:
        run_router(args.image, settings)
        run_extractor(args.image, doc_type, settings)
        run_judge(args.image, {"invoice_number": "TEST"}, settings)
    except GeminiCallError as exc:
        print(f"\nGemini call failed: {exc}", file=sys.stderr)
        if exc.request_summary:
            _print_section("Failed request summary", exc.request_summary)
        if exc.raw_response is not None:
            _print_section("Failed raw response", exc.raw_response)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
