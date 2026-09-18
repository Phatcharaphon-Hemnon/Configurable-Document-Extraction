---
description: Evidence-backed reports for this project (project-adapted, not upstream ECC)
agent: build
---

# /ecc-update-docs (project-local)

Project-adapted reporting. Structural reference: upstream ECC
`update-docs` command (MIT, see `.opencode/PROVENANCE.md`). This is NOT an
upstream ECC command. Per `AGENTS.md` rule 7, implementation work gets a
markdown doc under `docs/`.

Document: $ARGUMENTS

## Target

Dated report under `docs/reports/` (e.g. `extractor_timeout_phases1-4_2026-09-16.md`).

## Required contents

- Files changed and reasons (in-scope only).
- Corrected evidence and historical-setting uncertainties.
- Confirmed defects and fixes.
- Exact commands/tests executed and outcomes (real output, no invention).
- Review findings and remaining limitations.
- Prepared diagnostic command, caps, isolation, blocked prerequisites
  (if applicable).
- Rollback affecting only this task's changes.
- Workflows consulted vs tools/checks actually run (distinguish clearly).
