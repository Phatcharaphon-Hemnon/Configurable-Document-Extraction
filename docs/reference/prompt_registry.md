# Prompt Registry

> Module: `api/app/prompts/` · Added 2026-09-18 · replaces inline prompt
> constants in the agent modules.

## What it is

All LLM prompt templates live in versioned JSON files instead of inline
Python string constants:

| File | Consumer | Contents |
|---|---|---|
| `router.json` | `app/agents/router.py` | classification instruction (`template`) + optional filename line (`filename_suffix`) |
| `extractor.json` | `app/agents/extractors.py` | prompt assembly parts: `header`, `catalog_intro`, `examples_intro`, `rules`, `text_intro`, `image_note` |
| `judge.json` | `app/agents/judge.py` | ordered `instructions` list + dynamic intros: `canonical_records_intro`, `findings_intro`, `ocr_line`, `completeness_intro`, `source_text_intro`, `image_note` |
| `registry.py` | loader/API | validation, rendering, version reporting, content hash |

The validator (rule-based `ValidatorAgent`) calls no LLM and has no prompt —
it is intentionally outside the registry.

## API

```python
from app.prompts.registry import (
    get_prompt,          # PromptSpec for one agent
    active_versions,     # {"router": "v1", "extractor": "v1", "judge": "v1"}
    compound_prompt_version,  # content-hash cache version
)

spec = get_prompt("extractor")
spec.render("header", doc_label="invoice")     # literal {name} replacement
spec.parts["rules"]                            # raw template part
get_prompt("judge").instructions()             # ordered static lines
```

Rendering uses **literal `{placeholder}` replacement, not `str.format`** —
literal braces inside prompt text (e.g. the JSON table shape example in the
extractor rules) pass through untouched.

## Versioning model

- Each JSON file carries a human-readable `version` + `changelog` (history:
  what changed and when).
- `compound_prompt_version()` derives the machine version from template
  **content only** (`sha256` over canonicalized parts):
  `prompts-registry:<12 hex chars>`.
- **Any template edit changes the compound version automatically** →
  `ResultCache` fingerprints (`result_cache.py::PROMPT_VERSION`) and manifest
  keys invalidate; there is no manual bump to forget.
- Cosmetic edits (version labels, changelog text) do **not** invalidate
  computed results — only template content does.

## Where the version is recorded

| Surface | Field |
|---|---|
| Result cache fingerprint | `versions.prompts` in `config_fingerprint_dict()` |
| Extraction response / DB | `ExtractionResult.prompt_version` (schema default) |
| Langfuse trace metadata | `prompt_version` + `prompt_versions` (per-agent) |
| Eval report config table | `prompt_version` / `prompt_versions` (`run_eval.py`) |

## Editing a prompt

1. Edit the template part in the agent's JSON file (keep `{placeholders}`).
2. Append a changelog entry and bump the file's `version` label.
3. No further action: the cache version changes by itself; the next run
   recomputes affected results. Update the file's `changelog` so the bump is
   auditable.

Fail-fast: a missing file, corrupt JSON, id mismatch, or a missing required
part raises `PromptRegistryError` at import time — the API refuses to start
with a broken prompt set rather than falling back to silent defaults.

## Tests

`api/tests/test_prompt_registry.py` — load/validation, rendering (incl.
literal braces), compound version determinism + edit-invalidation + cosmetic
stability, `ExtractionResult.prompt_version` default, and fail-fast cases.

## Import guarantee

At introduction the templates were verified **byte-identical** to the
previous inline prompts (router both filename variants, extractor all
assembly variants incl. few-shot/empty-text/Thai, judge full + minimal
variants), so results were unchanged; only the cache version string moved
from the manual `prompts-v4-judge-coverage-cap` token to the derived
`prompts-registry:<hash>` (one-time cache invalidation by design).
