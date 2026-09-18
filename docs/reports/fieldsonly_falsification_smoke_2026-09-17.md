# Fields-only falsification smoke (2026-09-17): still censored at 300s

Tests the tables-nesting investigation's recommended next step: drop the
`tables` block from the extractor wire schema, keep everything else
identical (narrowed value union, strict:true), one bounded smoke.

## 1. Diff summary + tests

- `api/app/schemas/llm_schemas.py` — new `ExtractionFieldsOnlySchema`
  (fields-only, `extra="forbid"`); real `ExtractionResponseSchema`
  byte-untouched.
- `api/app/core/config.py` — additive `EXTRACTION_TABLES_ENABLED`
  (default true = production keeps tables; diagnostic sets false
  process-locally, never in `api/.env` which contains no such key).
- `api/app/agents/extractors.py` — `tables_enabled()` helper (missing
  attr = ON, legacy doubles safe); `_TABLES_RULE` extracted so
  `_build_prompt(..., include_tables=False)` drops the tables instruction
  (prompt and wire schema stay consistent); `_run_call` selects the
  variant schema only when explicitly disabled; `getattr` tables read
  (identical behavior on the default path).
- `api/tests/test_fields_only_schema.py` (new, 4 tests, RED-first):
  default schema keeps `tables`, variant rejects `tables` key, flag
  defaults ON, prompt rule follows the flag.
- `ruff check api/`: clean (remaining `extractors.py` LSP `.fields`
  notes are on untouched lines — pre-existing strictness).
  Full `pytest api/tests/`: **719 passed, 2 skipped** (715 + 4 new).
- Two implementation bugs caught by own checks and fixed in-test/in-code:
  helper placed mid-imports (E402), `rules` computed but not appended
  (F841), test asserted on a rule line that lives outside the tables
  rule, unused import (F401).

## 2. Live smoke outcome: STILL CENSORED at 300s

One new non-streaming dispatch, X51008142033, flag off for this run
only, 300s ceiling: router ok 91.28s, **extractor timeout at 300.1s**
(attempt 1/1), outcome partial, judge unavailable, 2 dispatches.
`extractor_prompt` 4490 chars (vs 8021 with tables) confirms the variant
was live. Total 395.9s, within 300/400/500 bounds. No confirm/apply.

## 3. Falsification reading

Per the investigation's own logic, completing would have confirmed
tables-nesting; staying censored **exonerates tables-nesting** (and the
value-union narrowing before it) as the stall mechanism. Next candidates
per the staged plan: strict-mode itself (extractor `json_object` probe)
or server-side config. **No further changes made automatically.**

## 4. Flag state confirmation

Temporary flag is ON by default and production schema untouched:
`EXTRACTION_TABLES_ENABLED` appears nowhere in `api/.env`; the variant
schema class is referenced only behind the explicit-off branch;
`get_settings()` defaults keep the full schema. Evidence:
`logs/calib-fieldsonly-20260917/`. No restarts beyond the smoke process,
no pushes.
