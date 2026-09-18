# ECC integration provenance (project-local adaptation)

- Upstream source: https://github.com/affaan-m/ECC (`.opencode/` tree, `main` branch)
- Fetched: 2026-09-16 (public files only: `.opencode/README.md`,
  `.opencode/opencode.json`, `.opencode/commands/{plan,tdd,code-review,verify,update-docs}.md`, `LICENSE`)
- Upstream license: MIT, Copyright (c) 2026 Affaan Mustafa (see `LICENSE.ecc-upstream`).
  The MIT notice is preserved verbatim in this directory as required.
- Scope of adaptation: command templates only. No plugin code, hooks, agents,
  skills, or global configuration were copied. No model/provider overrides,
  permission auto-approvals, runtime hooks, or multi-agent orchestration added.
- Naming: project commands use the `ecc-` prefix (`ecc-debug`, `ecc-tdd`,
  `ecc-code-review`, `ecc-verify`, `ecc-update-docs`) to avoid collisions with
  any existing or future `plan` / `tdd` / `code-review` / `verify` / `update-docs`
  commands. There was no pre-existing `opencode.json` / `opencode.jsonc` /
  `.opencode/` in this checkout, so nothing was merged or overwritten.
- Layout: root `opencode.json` registers the five commands; templates live in
  `.opencode/ecc/commands/ecc-*.md`. The config sets commands only — no model,
  provider, permission, hook, or plugin keys (verified by parse check).
- Adapted project commands vs upstream ECC commands: files under
  `.opencode/commands/ecc-*.md` are project-specific rewrites for this
  Python/FastAPI + React codebase (pytest, ruff, npm build, SQLite history,
  offline-first, no gold-answer leakage). They are NOT upstream ECC commands;
  upstream originals were used as structural reference only and are not vendored.
- Runtime registration: validated by JSON parsing of `.opencode/opencode.json`
  (see verification note in the implementation report). Loading of the config
  by the OpenCode CLI at session start is marked UNVERIFIED unless confirmed
  with installed tooling in a live session.
