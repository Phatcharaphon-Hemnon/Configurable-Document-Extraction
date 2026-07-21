# ADR-0001: Schema Mode (strict / open)

**Date**: 2026-07-19

**Decision**: The system supports two runtime modes, selectable via the `SCHEMA_MODE`
environment variable (`"strict"` or `"open"`, default `"strict"`).  Strict mode
restores the original assignment specification: documents are constrained to the
three catalog-backed types (invoice, po, delivery_note) with blocking validation
that marks a document invalid when a catalog-required field is missing.  Open mode
retains the later open-schema refactor where `doc_type` is free-form and validation
is soft/informational.  Both code paths coexist; strict is the default because the
assignment rubric evaluates against the three fixed types, while open mode is
preserved as an opt-in extension for broader document coverage.
