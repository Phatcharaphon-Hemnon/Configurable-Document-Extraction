# Narrowed-schema smoke test (2026-09-17): still censored at 300s

Falsification run for the schema-cost investigation's driver #1
(open `list`/`dict` union branches in `ExtractedFieldEntry.value`).

## 1. List/dict scan: CLEAR, no blocking fields

Scanned field catalogs (3 files), eval predictions, FUNSD/SROIE
annotation files (hits were annotation geometry — boxes/words — not
field values), History payloads (9 jobs), and test fixtures
(`"value": [` grep): **zero list/dict-shaped field values anywhere**.
Only `job_repository.py:145` defensively serializes them; no real data
ever contains them. Tables travel in the separate `tables` block.

## 2. Diff + tests

- `api/app/schemas/llm_schemas.py:23`: `value:
  Optional[Union[str, float, int, list, dict]]` →
  `Optional[Union[str, float, int]]`, with a comment recording the
  verification date. Tables, strict-tier logic, regions, timeouts
  untouched. No fixture updates required (nothing used the removed
  branches).
- Honest caveat: serialized schema shrank only 3757 → 3674 chars. Char
  count was never the mechanism — the open-ended recursive union
  branches were. The live run, not char count, is the falsifier.
- `ruff check api/`: clean. Full `pytest api/tests/`: **715 passed,
  2 skipped** (pre-existing).

## 3. Live smoke outcome: STILL CENSORED at 300s

One new non-streaming dispatch, X51008142033, 300s ceiling: router ok
91.29s, **extractor timeout at 300.1s** (attempt 1/1), outcome partial,
judge unavailable, 2 dispatches, `extractor_prompt` 8021 chars. Total
396.0s, within all bounds (300/400/500). No confirm/apply; `api/.env`
untouched.

## 4. Next per the staged plan: driver #2 (tables nesting)

Narrowing the value union alone did not resolve the stall. Per the
investigation's staged plan, the next candidate is driver #2 —
`tables: list[ExtractedTable]` with `rows: list[list[TableCell]]` plus
per-cell `evidence_refs`. **No further changes made automatically**;
driver #2 work requires separate authorization. Evidence:
`logs/calib-narrowed-20260917/`.
