# Judge Review (Tolerance Alignment + Grouped UI)

> Last updated: 2026-09-07. Fixes the Judge contradicting the validator
> (e.g. `seller_name` substring "allowed" but `invoice_number` substring
> flagged `error`) and the same root cause showing in two alarming panels.

## Background

The pipeline flags problems in two independent stages:

1. **Validator** (deterministic): every value needs a `source_span` found in
   the OCR text. Long spans (> 3 tokens) pass with ≥ 0.75 token overlap;
   `line_items` cells are checked per-cell with numeric magnitude matching
   (see `docs/line_items_evidence.md`).
2. **Judge** (LLM, `api/app/agents/judge.py`): runs whenever the extraction
   is not clean (any validation error, incomplete, low confidence, or
   non-verbatim numeric span) and returns `score` + per-field `issues`.
   Only `score < 0.7` (`JUDGE_PASS_SCORE`) sets `needs_review` — severities
   are advisory.

The old judge prompt said *"a value whose span does not appear verbatim is
suspect"* with no overlap exception, so a long non-verbatim span the
validator **passed** could be flagged by the judge — and the LLM applied
substring tolerance inconsistently across fields.

## Current rules

- **Value-in-span is supported**: when the value appears inside its own
  quoted `source_span` and that span appears in the source text, the judge
  must grade it `info` at most, never `error` (`judge.py::evaluate`).
- **`error` is reserved** for values not derivable from their span, or
  spans not found in the source text.
- **Extractor dense-line rule** (`extractors.py::_COMMON_RULES`): IDs,
  reference numbers and dates must be exact full-token copies — never
  concatenate fragments across spaces/line-breaks on number-dense lines
  (e.g. `Ref No … 05/ 56200209142193 06/04717 …` previously produced the
  garbage date `05/56200209142193`). Dates must be plausible calendar
  dates; otherwise omit the field and lower confidence.

## Grouped review UI (`web/src/components/ExtractionTab.tsx`)

`validation_errors` (free-form strings) are attributed to fields by
longest-name match (`attributeField`) and merged with `judge.issues`
(structured `field`) into one **Needs Review** callout grouped per field,
with the judge score chip and notes on top. Unattributable messages fall
under `general`. A passing judge with no validation errors keeps the
existing green `Judge Review` callout unchanged.

## Table-span case (`purchase_orders_10248.pdf`, 2026-09-07)

Table PDFs linearize in OCR as headers-then-values (`Order ID Order Date
Customer Name` … `10248 2016-07-04 Paul Henriot`), so extractor spans like
`'Order ID 10248'` or `'Customer Name Paul Henriot'` are fabricated evidence
even when the value is correct. Policy (per user decision): validator stays
**strict** — label-glued spans fail. The fix is extractor-side
(`extractors.py::_COMMON_RULES`): quote the bare cell value alone.

Same document showed required-but-absent guessing (`currency: USD` with a
meta-commentary span; a `total_amount` matching no line math — the doc
prints no currency and no total). Policy: omit absent fields even when
required (honest `Missing required field` beats hallucination); totals may
be computed only as `qty × unit` per row, summed, with the rows as span and
lowered confidence.

## Tests

`api/tests/test_review_contracts.py`: log-filter drop/keep cases (tuple
args + fallback message format), judge value-in-span + severity-cap prompt
assertions, extractor exact-token prompt assertions.
