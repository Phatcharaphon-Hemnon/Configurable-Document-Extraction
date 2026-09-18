---
description: Regression-first bug fixes for this FastAPI+React project (project-adapted, not upstream ECC)
agent: build
---

# /ecc-tdd (project-local)

Project-adapted TDD workflow. Structural reference: upstream ECC `tdd`
command (MIT, see `.opencode/PROVENANCE.md`). This is NOT an upstream ECC
command. Coverage target here is regression coverage of the defect, not a
fixed 80% threshold — do not invent coverage thresholds.

Fix: $ARGUMENTS

## Cycle (MANDATORY): RED → GREEN → REFACTOR → REPEAT

1. **RED**: write a failing regression test FIRST (under `api/tests/`,
   mocked providers, temporary storage — never runtime `data/` DBs, never
   live inference, never gold answers as prompt/OCR input).
2. **GREEN**: minimal fix. Preserve failure presentation, Judge-unavailable
   status, evidence/partial results, cache exclusion of failures, legacy
   result loading, provider/model/budgets/validation, concurrency 1,
   regions default-off.
3. **REFACTOR**: keep tests green, keep diffs focused, preserve unrelated
   working-tree changes.

## Project test commands

- Focused: `source .venv/bin/activate && python -m pytest api/tests/test_<name>.py -q` (run from repo root; `pytest.ini` sets `pythonpath=api`)
- Lint: `ruff check api/`
- Full backend: `python -m pytest api/tests/ -q`
- Frontend (only if UI changed): `cd web && npm run build`

## Constraints

- No timeout raises as a speed fix, no blind output-capacity cuts, no
  model/provider switches, no weakened validation.
- No timeout retry/format fallback/corrective-generation changes unless the
  confirmed defect requires it; count them accurately if touched.
- Do not claim latency improvement from offline region size measurements.
