---
description: Correctness/security review for this FastAPI+React project (project-adapted, not upstream ECC)
agent: build
---

# /ecc-code-review (project-local)

Project-adapted review. Structural reference: upstream ECC `code-review`
command (MIT, see `.opencode/PROVENANCE.md`). This is NOT an upstream ECC
command.

Review: $ARGUMENTS

## Steps

1. `git diff --name-only HEAD` plus untracked files in scope; ignore
   unrelated working-tree changes.
2. Analyze each in-scope file; generate a structured report; fix confirmed
   findings within scope.

## Check categories (project-specific)

### Correctness (CRITICAL)
- [ ] Shared mutable state / counter leakage across jobs or event loops
- [ ] Double-debited or uncounted HTTP dispatches
- [ ] Cancellation/shutdown misclassified as timeout
- [ ] Region fallback unpack or typed-outcome mismatches on any return path
- [ ] Page isolation broken (page N metadata overwriting page M)
- [ ] Legacy saved results no longer loadable

### Cache & failure semantics (HIGH)
- [ ] Failed extractions displayable or cacheable as successful
- [ ] Completed-result cache eligibility changed for existing paths
- [ ] Partial results or accepted/rejected evidence dropped

### Security (CRITICAL)
- [ ] Secrets printed in logs/errors (`LLM_API_KEY`, native key vars)
- [ ] Gold annotations / box transcripts fed into prompts or OCR
- [ ] Prompt-injection guard (`sanitize_document_text`) bypassed
- [ ] Path traversal in source/preview serving

### Scope hygiene (MEDIUM)
- [ ] Accidental provider/model/budget/validation changes
- [ ] Regions enabled by default or concurrency changed
- [ ] Runtime-storage writes in tests or scripts
- [ ] Unrelated working-tree files touched

## Report format

For each finding: **[SEVERITY]** `path:line` — Issue / Fix. End with a
decision: block (CRITICAL/HIGH open) or pass with optional follow-ups.
