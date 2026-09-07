# Schemas — LLM output contracts

> Last updated: 2026-09-09. Strict Pydantic contracts in
> `api/app/schemas/llm_schemas.py` — the shapes the LLM must return.
> API/result shapes: `docs/extraction_response_contract.md`; request
> budgets: `docs/llm_request_queue.md`.

## What schemas are in this project

A **schema** = a Pydantic contract the pipeline enforces at runtime:
`Client` sends the schema to the model (tier 1 strict `json_schema`) and
`output_guard.validate_output_schema()` validates the reply against it —
contract violations become explicit errors, never silent corruption.

## `schemas/llm_schemas.py` — the three LLM contracts

- `RoutingResponseSchema`: `doc_type` (Literal invoice/purchase_order/
  delivery_note), `language`, `confidence` 0–1, `reason`.
- `ExtractedFieldEntry`: `name`, `value` (str/float/list/dict/None),
  `confidence` (default 0.5), `source_span`; `ExtractionResponseSchema`
  wraps `fields` with `extra=forbid` (unknown keys rejected).
- `JudgeIssueEntry` (`field`, `message`, `severity` info/warning/error);
  `JudgeResponseSchema`: `score` 0–1, `issues`, `notes`.

`_pydantic_to_json_schema()` (in `client.py`) strips Pydantic-only keys and
patches every object (`additionalProperties: false`, all properties
required) for strict mode.

## Verify

```bash
source .venv/bin/activate
python -m pytest api/tests/test_client_schema.py api/tests/test_schemas.py -q
```
