---
description: Actual verification for this FastAPI+React project (project-adapted, not upstream ECC)
agent: build
---

# /ecc-verify (project-local)

Project-adapted verification. Structural reference: upstream ECC `verify`
command (MIT, see `.opencode/PROVENANCE.md`). This is NOT an upstream ECC
command. Only real tool output counts — never invent results.

Verify: $ARGUMENTS

## Commands (repo root unless noted; resolve `.venv` first)

1. Focused regressions: `source .venv/bin/activate && python -m pytest api/tests/test_<name>.py -q`
2. Backend lint: `ruff check api/`
3. Full backend suite: `python -m pytest api/tests/ -q`
4. Frontend (only if UI changed): `cd web && npm run build` (typecheck+build);
   mocked UI checks where available. Do not download browsers.

## Rules

- Do not download missing dependencies or browsers; report unavailable
  checks as blocked.
- Do not weaken assertions, skip failures, or invent coverage thresholds.
- Report table: check / status PASS-FAIL-BLOCKED / notes (with real output).

## Report format

Status: PASS / FAIL / BLOCKED. Per-check rows plus action items for any
FAIL. BLOCKED items name the missing prerequisite.
