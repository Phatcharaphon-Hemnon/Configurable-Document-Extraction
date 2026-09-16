# Source-language dynamic tables and evidence

`TableColumn`, `TableCell`, `ExtractedTable` and `SourceReference` live in
`app/schemas/documents.py`. The extraction JSON has separate `fields` and `tables`.
Each table has an ordered list of `{key, label}` columns and rows of cells carrying
`column`, `value`, `confidence` and `source_span`. Printed labels retain their
original language; unlabeled columns use positional keys. There is no fixed
four-column limit. Scalars retain exact normalized catalog names, without aliases.

The extractor prompt requests all printed columns and rows, including discounts,
product codes and tax columns when present. It omits absent values and avoids
inventing totals, due dates, email addresses or buyer identities. The UI renders
columns from the response and permits horizontal scrolling. Old serialized table
fields still render through a compatibility path using the union of row keys.

Validator checks every typed cell's evidence/value/confidence, duplicate or unknown
column references, and supported quantity/price/discount arithmetic. Scalar
identifiers must remain strings. Evidence must be a normalized substring of the
OCR text, rather than approximate token overlap. Numeric evidence compares whole
numeric values: `6` is not evidence for `6.9`. New catalog keys are registered only
when their values are supported by evidence.

Judge receives both scalar fields and tables. Clean high-confidence outputs can
skip its LLM call; explicit statuses distinguish passed, flagged, skipped and
unavailable. Missing Judge or validation problems preserve `needs_review`.
Required-field coverage measures catalog coverage, not extraction accuracy.

These changes support the eight printed columns of the Thai invoice. They do not
guarantee the configured text model will reconstruct them accurately: inspect the
live evaluation report for measured table accuracy.
