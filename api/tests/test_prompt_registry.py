"""Tests for the prompt registry (api/app/prompts/).

Covers: load + validation, placeholder rendering with literal braces,
content-derived compound version (result-cache invalidation), fail-fast on
corrupt/missing registry files, and the ExtractionResult.prompt_version
default.
"""

from __future__ import annotations

import json

import pytest

from app.prompts import registry as pr
from app.prompts.registry import (
    PromptRegistryError,
    active_versions,
    compound_prompt_version,
    get_prompt,
)


def test_registry_loads_all_three_agents():
    assert set(active_versions()) == {"router", "extractor", "judge"}
    for agent_id, version in active_versions().items():
        assert version, f"{agent_id} has empty version"
        spec = get_prompt(agent_id)
        assert spec.id == agent_id
        assert spec.changelog, f"{agent_id} has no changelog"


def test_render_replaces_placeholders_only():
    spec = get_prompt("extractor")
    out = spec.render("header", doc_label="purchase order")
    assert out == "Extract data from this purchase order."
    # Literal braces in the rules (JSON shape examples) survive rendering.
    rules = spec.render("rules")
    assert "{name, columns:[{key,label}], rows:[[" in rules


def test_render_missing_part_fails_fast():
    spec = get_prompt("router")
    with pytest.raises(PromptRegistryError):
        spec.render("no_such_part")


def test_judge_instructions_are_ordered_non_empty():
    instructions = get_prompt("judge").instructions()
    assert instructions[0] == "You are a strict document-extraction judge."
    assert all(line.strip() for line in instructions)


def test_compound_version_is_deterministic_and_content_derived():
    v1 = compound_prompt_version()
    v2 = compound_prompt_version()
    assert v1 == v2
    assert v1.startswith("prompts-registry:")
    assert len(v1) == len("prompts-registry:") + 12

    # A template edit changes the compound version (cache invalidation).
    original = pr._SPECS["router"]
    mutated = pr.PromptSpec(
        id="router",
        version=original.version,
        parts={**original.parts, "template": original.parts["template"] + "\nBe stricter."},
    )
    pr._SPECS["router"] = mutated
    try:
        assert compound_prompt_version() != v1
    finally:
        pr._SPECS["router"] = original

    # Cosmetic-only changes (version label) do NOT invalidate results.
    relabeled = pr.PromptSpec(
        id="router", version="v999", parts=dict(original.parts)
    )
    pr._SPECS["router"] = relabeled
    try:
        assert compound_prompt_version() == v1
    finally:
        pr._SPECS["router"] = original


def test_result_cache_prompt_version_matches_registry():
    from app.services.result_cache import PROMPT_VERSION

    assert PROMPT_VERSION == compound_prompt_version()


def test_extraction_result_carries_prompt_version_default():
    from app.schemas.documents import ExtractionResult

    result = ExtractionResult(doc_type="invoice", fields=[])
    assert result.prompt_version == compound_prompt_version()
    # Old payloads without the field still validate (backward compatible).
    legacy = json.loads(json.dumps({"doc_type": "invoice", "fields": []}))
    assert ExtractionResult.model_validate(legacy).prompt_version == compound_prompt_version()


@pytest.fixture()
def broken_registry(tmp_path, monkeypatch):
    """Point the loader at a temp registry dir; caller writes bad files."""
    monkeypatch.setattr(pr, "PROMPTS_DIR", tmp_path)
    return tmp_path


def _write(tmp_path, agent_id, payload):
    (tmp_path / f"{agent_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_registry_file_fails_fast(broken_registry):
    for agent_id in ("router", "extractor"):
        _write(broken_registry, agent_id, {
            "id": agent_id, "version": "v1",
            "parts": {part: "x" for part in pr.REQUIRED_PARTS[agent_id]},
        })
    with pytest.raises(PromptRegistryError, match="missing"):
        pr._load_spec("judge")


def test_id_mismatch_fails_fast(broken_registry):
    _write(broken_registry, "router", {
        "id": "extractor", "version": "v1",
        "parts": {part: "x" for part in pr.REQUIRED_PARTS["router"]},
    })
    with pytest.raises(PromptRegistryError, match="id mismatch"):
        pr._load_spec("router")


def test_missing_part_fails_fast(broken_registry):
    _write(broken_registry, "router", {"id": "router", "version": "v1", "parts": {"template": "x"}})
    with pytest.raises(PromptRegistryError, match="missing parts"):
        pr._load_spec("router")


def test_corrupt_json_fails_fast(broken_registry):
    (broken_registry / "router.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PromptRegistryError, match="unreadable"):
        pr._load_spec("router")


def test_unknown_agent_rejected():
    with pytest.raises(PromptRegistryError):
        get_prompt("chat")
