"""Prompt registry: versioned prompt templates for the LLM agents.

Prompts live in JSON files next to this module (one per agent) instead of
inline constants in the agent code. Each file carries an id, a human-readable
version, the template parts, and a changelog.

Guarantees:
- Loaded and validated once at import; a missing/corrupt file or a missing
  template part fails fast with a clear error (no silent fallback prompts).
- Templates render with named ``{placeholder}`` slots via literal replacement
  (NOT str.format) so literal braces in prompt text (JSON shape examples)
  survive untouched.
- ``compound_prompt_version()`` derives the result-cache prompt version from
  the template CONTENT: any edit to a template changes the compound version
  and invalidates result-cache fingerprints automatically. Cosmetic JSON
  changes (changelog/version labels) do not invalidate results.

Agent wiring (single consumers):
- router  -> ``router.json``  (template + filename_suffix)
- extractor -> ``extractor.json`` (header/catalog_intro/examples_intro/rules/
  text_intro/image_note)
- judge   -> ``judge.json``   (instructions list + dynamic intro templates)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent

# Required template parts per agent id. A registry file missing any of these
# parts is rejected at load time (fail fast, no silent fallback).
REQUIRED_PARTS: dict[str, set[str]] = {
    "router": {"template", "filename_suffix"},
    "extractor": {"header", "catalog_intro", "examples_intro", "rules", "text_intro", "image_note"},
    "judge": {"instructions", "canonical_records_intro", "findings_intro", "ocr_line", "completeness_intro", "source_text_intro", "image_note"},
}

_AGENT_IDS = ("router", "extractor", "judge")


class PromptRegistryError(RuntimeError):
    """Raised when the prompt registry is missing, corrupt, or incomplete."""


@dataclass(frozen=True)
class PromptSpec:
    """One agent's registered prompt template."""

    id: str
    version: str
    parts: dict[str, Any]
    changelog: list[str] = field(default_factory=list)

    def render(self, part: str, **values: str) -> str:
        """Render one template part by literal ``{name}`` replacement.

        Literal braces that are NOT named placeholders (e.g. the JSON shape
        example inside the extractor rules) pass through untouched.
        """
        try:
            template = self.parts[part]
        except KeyError as exc:
            raise PromptRegistryError(f"prompt '{self.id}' has no part '{part}'") from exc
        if not isinstance(template, str) or not template:
            raise PromptRegistryError(f"prompt '{self.id}' part '{part}' is empty")
        out = template
        for key, value in values.items():
            out = out.replace("{" + key + "}", value)
        return out

    def instructions(self) -> list[str]:
        """Ordered static instruction lines (judge)."""
        rows = self.parts.get("instructions")
        if not isinstance(rows, list) or not all(isinstance(r, str) and r for r in rows):
            raise PromptRegistryError(f"prompt '{self.id}' part 'instructions' must be a non-empty list of strings")
        return list(rows)


def _load_spec(agent_id: str) -> PromptSpec:
    path = PROMPTS_DIR / f"{agent_id}.json"
    if not path.is_file():
        raise PromptRegistryError(f"prompt registry file missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PromptRegistryError(f"prompt registry file unreadable: {path} ({exc})") from exc
    if raw.get("id") != agent_id:
        raise PromptRegistryError(f"prompt registry id mismatch in {path}: {raw.get('id')!r} != {agent_id!r}")
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise PromptRegistryError(f"prompt registry file {path} has no version")
    parts = raw.get("parts")
    if not isinstance(parts, dict) or not parts:
        raise PromptRegistryError(f"prompt registry file {path} has no parts")
    missing = REQUIRED_PARTS[agent_id] - set(parts)
    if missing:
        raise PromptRegistryError(f"prompt registry file {path} missing parts: {sorted(missing)}")
    changelog = raw.get("changelog") or []
    if not isinstance(changelog, list):
        raise PromptRegistryError(f"prompt registry file {path} changelog must be a list")
    return PromptSpec(id=agent_id, version=version, parts=parts, changelog=list(changelog))


_SPECS: dict[str, PromptSpec] = {agent_id: _load_spec(agent_id) for agent_id in _AGENT_IDS}


def get_prompt(agent_id: str) -> PromptSpec:
    """Return the active registered prompt for one agent (router/extractor/judge)."""
    try:
        return _SPECS[agent_id]
    except KeyError as exc:
        raise PromptRegistryError(f"unknown prompt agent id: {agent_id!r}") from exc


def active_versions() -> dict[str, str]:
    """Human-readable active version per agent (for reports/metadata)."""
    return {agent_id: spec.version for agent_id, spec in _SPECS.items()}


def _content_hash() -> str:
    """Deterministic hash over template CONTENT only (sorted, canonical JSON).

    Version labels and changelog entries are excluded: bumping a label
    without touching templates must not invalidate computed results.
    """
    payload = {agent_id: _SPECS[agent_id].parts for agent_id in _AGENT_IDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def compound_prompt_version() -> str:
    """Result-cache prompt version derived from registry content.

    Any template edit changes this string, so result-cache fingerprints
    and region fingerprints invalidate automatically — no manual bump.
    """
    return f"prompts-registry:{_content_hash()[:12]}"
