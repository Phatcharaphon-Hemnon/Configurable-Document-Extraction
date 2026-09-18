"""Adversarial injection-corpus regression for the prompt-injection guard.

Every payload in ``fixtures/injection_corpus.txt`` must be neutralized by
``sanitize_document_text``: after sanitization no injection pattern may
remain (``is_suspicious`` -> False). Legitimate business content (IDs,
amounts, Thai text, table pipes) must survive sanitization untouched so
extraction quality is not harmed by the guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.security import MAX_PROMPT_CHARS, is_suspicious, sanitize_document_text

CORPUS = Path(__file__).parent / "fixtures" / "injection_corpus.txt"


def _payloads() -> list[tuple[str, str]]:
    lines = [
        line.strip()
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    out: list[tuple[str, str]] = []
    for line in lines:
        category, _, payload = line.partition("|")
        out.append((category.strip(), payload.strip()))
    assert out, "injection corpus is empty"
    return out


def test_corpus_has_full_coverage():
    categories = {category for category, _ in _payloads()}
    assert {
        "override", "role", "marker", "exfil", "action", "secrets", "tags", "mixed", "case",
    } <= categories
    assert len(_payloads()) >= 30


@pytest.mark.parametrize(("category", "payload"), _payloads(), ids=[c for c, _ in _payloads()])
def test_every_payload_is_neutralized(category, payload):
    assert is_suspicious(payload), f"{category}: payload should trip detection before sanitize"
    cleaned = sanitize_document_text(payload)
    assert not is_suspicious(cleaned), (
        f"{category}: injection survived sanitization: {cleaned!r}"
    )
    # Neutralization marker must appear for every caught payload.
    assert "[redacted-injection-attempt]" in cleaned


def test_legitimate_business_content_survives():
    legit = [
        "Invoice 002043319-W | Date: 2016-07-15 | Total 1,234.56 THB",
        "รหัสผู้เสียภาษี 1234567890123 ชื่อร้าน บริษัท ตัวอย่าง จำกัด",
        "PO 10248 | QTY 12 | Unit price 825.00 | Amount 9,900.00",
        "Tax ID 1234567890123 | Branch 00001 | Tel 02-123-4567",
        "Delivery note DN-5533 | Signed: received in good condition",
    ]
    for text in legit:
        cleaned = sanitize_document_text(text)
        assert not is_suspicious(cleaned)
        for token in ("002043319-W", "1,234.56", "10248", "1234567890123"):
            if token in text:
                assert token in cleaned, f"business token {token!r} lost: {cleaned!r}"
        if "รหัสผู้เสียภาษี" in text:
            assert "รหัสผู้เสียภาษี" in cleaned


def test_length_cap_truncates_oversized_document_text():
    cleaned = sanitize_document_text("x" * (MAX_PROMPT_CHARS + 5_000))
    assert len(cleaned) <= MAX_PROMPT_CHARS + len(" …[truncated]")
    assert cleaned.endswith("…[truncated]")


def test_pipeline_entry_points_wrap_document_text():
    """Every agent must route document text through the sanitizer.

    Source-level guard (mirrors the hallucination-control tests): the agent
    modules may not embed raw document text into a prompt without
    sanitize_document_text.
    """
    import inspect

    from app.agents import extractors, judge, router

    for module, func_name in (
        (router, "RouterAgent.classify"),
        (extractors, "_build_prompt"),
        (judge, "JudgeAgent.evaluate"),
    ):
        if func_name == "_build_prompt":
            src = inspect.getsource(getattr(extractors, "_build_prompt"))
        else:
            cls_name, method = func_name.split(".")
            src = inspect.getsource(getattr(getattr(module, cls_name), method))
        # The function that builds the prompt either calls the sanitizer
        # itself or receives text that was sanitized by the caller (the
        # calling sites in the same module do it).
        module_src = inspect.getsource(module)
        assert "sanitize_document_text" in module_src, (
            f"{module.__name__} no longer references the injection guard"
        )
        assert "data only, never instructions" in module_src or "sanitize_document_text" in src, (
            f"{func_name} lost the sanitizer/data-only contract"
        )
