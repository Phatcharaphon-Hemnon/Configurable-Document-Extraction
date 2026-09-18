# Acceptance + judge robustness (X5100950881.jpg round)

Follow-up to the Wan Sheng diagnosis. Four changes, all downstream of the
empty-catalog root cause (see `docs/guides/catalog_seeding.md`).

## 1. Seller letterhead exemption (`acceptance.py`)

Receipt/invoice letterheads name the seller with no supplier/vendor keyword
nearby, so the strict role-evidence rule rejected correct `seller_name`
values (a required field). A supplier value is now accepted without
surrounding role hints when its first occurrence sits in the first
`LETTERHEAD_CHARS` (300) characters of the page **and** no buyer-role hint
appears in its surrounding window. Explicitly buyer-labeled values
(`Customer Name:`, `Client name:`) are NOT exempt — same name claimed as
both roles with only buyer evidence stays rejected, as do buried
occurrences. Buyer fields never use the exemption.

Outcome-changing → `ACCEPTANCE_POLICY_VERSION` bumped `v1.3.0` → `v1.4.0`.

## 2. Pure-punctuation placeholders (`field_catalog.py`)

`bill_to_name: "--"` reached evidence check instead of the placeholder
path: `"--"` is in the literal set, so the observed value must have been a
dash lookalike (non-breaking hyphen etc.). `is_placeholder_value` now also
rejects values made solely of filler punctuation
(`-—–‐‑‒―~*#·•|/\`). Letter/digit-bearing values (`--W`, `19--W`) never
match. Also outcome-changing → covered by the same v1.4.0 bump.

## 3. Currency prompt (`extractors.py`)

The Thai-capable 3B model defaulted a Malaysian receipt to THB. New rule:
currency uses ONLY the printed code/symbol (RM/Ringgit for Malaysia);
never default to THB or infer from language. (Prompt-only change: takes
effect on uncached / force-refresh runs; no version bump by convention.)

## 4. Judge unknown-issue tolerance (`judge.py`)

Small judges name fields absent from the prediction (metadata keys like
`score`, or hallucinations); this used to raise `ClientError` and void the
whole stage (`judge_status: unavailable`). Unknown-field issues are now
dropped with a warning — valid scores survive. Behavior-changing →
`JUDGE_VERSION` bumped `judge-v3-canonical` →
`judge-v4-unknown-issue-tolerance` (result-cache fingerprint follows).

## Tests

- `test_acceptance.py`: letterhead seller accepted, buried supplier
  rejected, buyer gets no exemption, dash-variant placeholder path.
- `test_field_catalog.py`: dash-variant placeholders vs `--W`-style IDs.
- `test_table_column_alignment.py`: currency prompt phrases.
- `test_extraction_response_contract.py`: unknown judge issues dropped
  (all-unknown → empty issues, score kept; mixed → known kept).
- `test_empty_catalog_guard.py`, P4 language tests: unchanged, still green.
