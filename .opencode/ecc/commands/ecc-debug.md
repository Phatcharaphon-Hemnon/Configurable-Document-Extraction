---
description: Evidence-driven debugging for this FastAPI+React extraction project (project-adapted, not upstream ECC)
agent: build
---

# /ecc-debug (project-local)

Project-adapted debugging workflow for Configurable Document Extraction.
Structural reference: upstream ECC `plan`/`verify` commands (MIT, see
`.opencode/PROVENANCE.md`). This is NOT an upstream ECC command.

Investigate: $ARGUMENTS

## Rules (project-specific, mandatory)

1. **Evidence before synthesis.** Reconcile stored job payloads
   (`data-local/extraction.db` / `data/extraction.db` — never both), existing
   logs, and code. Mark missing measurements `unknown`; never infer token
   progress from CPU or zero response bytes from absent usage.
2. **Separate effective settings from historical settings.** Code defaults
   (`api/app/core/config.py`) differ from effective `.env` values
   (e.g. `EXTRACTION_MAX_TOKENS` default 8000 vs effective 3000). Settings at
   failure time are unknown unless stored with the job.
3. **No secrets in output.** Never print `LLM_API_KEY` or native key vars;
   report provider/endpoint/model names and numeric limits only.
4. **No gold leakage.** Never feed `ground_truth/*.box.txt`,
   `*.entities.json`, or known answers into prompts or OCR output.
5. **No live inference, restarts, downloads, History deletion, pushes.**

## Steps

1. Identify the serving implementation: PID/cwd/entrypoint (if observable
   without restart), file mtimes vs process start, effective
   endpoint/model/deadlines/token limits/region flag. State verification
   limits (mtimes are inference, not proof of loaded code).
2. Trace the exact failed request: job ID, source identity, queue wait, OCR
   duration, per-attempt durations, full-page vs region path, region
   count/eligibility/fallback reason, prompt/schema sizes, output budget,
   exception chain (HTTP timeout vs stage/job deadline), model-load and
   memory/swap evidence if available at the time.
3. Correct attribution: missing usage tokens = "no completed
   response/usage recorded". Attribute timeout source from
   exception/deadline/timing evidence, not null status/code alone.
   Cancellation/shutdown must not be reported as timeout.
4. Report: confirmed cause OR explicitly unresolved hypotheses with the
   measurements that would resolve them.
