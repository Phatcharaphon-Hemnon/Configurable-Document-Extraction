# Extraction response and Judge grounding

Every structured generation includes a compact JSON schema in its prompt,
including strict and json_object modes. This matters for compatible providers
that return JSON without enforcing the requested schema. The schema is not a
few-shot example and contains no fabricated document values.

Extraction responses must contain an explicit `fields` list. Unknown top-level
keys are rejected: `{}`, `{"score":0}`, or a Judge-shaped object are invalid
extractions, rather than silently becoming an empty list through a default.
Explicit `{"fields":[]}` is valid JSON but the pipeline reports an extractor
failure when no usable fields remain after filtering. It never guesses required
values, registers empty results, or sends an empty prediction to the Judge.

When format validation fails, the final fallback regenerates from the original
prompt (and original image, for legacy image calls), with the same schema.
It does not reinterpret the previous model output as source evidence. The
four-request budget and shared provider limiter remain in force.

The Judge distinguishes review metadata (`score`, `issues`, `notes`) from
predicted fields. Issue names must exactly match a supplied prediction name;
unknown names invalidate the review, and the pipeline flags Judge unavailable
and needs_review while retaining extracted fields. Direct Judge calls with no
predicted values return a deterministic zero score and no invented issues,
without an LLM request.

Regression tests exercise malformed/wrong-shaped JSON, json_object prompts,
source-preserving retries, empty extractions, and unsupported Judge issue names.
This protects result contracts; extraction accuracy still depends on readable
OCR and the selected model. Existing saved results are not automatically rerun.
