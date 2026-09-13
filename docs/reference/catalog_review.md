# Catalog Review — Curating AI-Discovered Fields

> When the extractor finds a clearly labeled value with no catalog name, it
> invents a `snake_case` name (`new_field: true`) and the pipeline appends it
> to that doc type's catalog JSON with `"source": "ai_discovered"`.

## Review helper

```bash
source ../.venv/bin/activate   # from api/
python scripts/review_discovered_fields.py            # readable tables
python scripts/review_discovered_fields.py --json     # machine-readable
```

Read-only. Lists discovered fields per doc type with type/required/rule,
and flags suspicious ones:

| Flag | Meaning |
|------|---------|
| `LONG>30` | Overlong name, usually OCR noise glued together |
| `DIGITS` | Mostly digits, barely any letters |
| `GENERIC` | Vague name (`total`, `amount`, …) that likely duplicates a catalog field |
| `NEAR-DUP~x` | Within edit distance of existing catalog field `x` — probable duplicate |

## Curating

Edit `api/app/data/knowledge_base/field_catalog/<type>_fields.json`
directly: delete junk, rename near-dups to the canonical name, and set
`type` / `required` / `validation_rule` on keepers. The service picks up
edits automatically (mtime-based cache invalidation). No restart needed.

## Auto-add gate (for reference)

A discovered field is registered only when all hold
(`is_registerable_new_field`): non-placeholder value, clean `snake_case`
name, confidence ≥ 0.6. The extractor prompt (`_COMMON_RULES` in
`api/app/agents/extractors.py`) explicitly forbids dropping labeled values
just because they are not in the catalog — verified live with a synthetic
`Loyalty Earned: 250 PTS` label, which registered as `loyalty_earned`.
