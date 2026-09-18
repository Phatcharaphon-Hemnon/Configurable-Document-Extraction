# Table column-key alignment

Failure mode: the extractor LLM declares table columns with one key
vocabulary but sends row cells with another (e.g. columns
`description/qty/total`, cells `item/quantity/amount`). Every row then
fails `set(row_keys) != set(col_keys)` and the whole table is withheld
("table withheld: no fully grounded rows").

## Primary fix — prompt (`api/app/agents/extractors.py`)

`_COMMON_RULES` now requires `columns[].key` and `rows[][].column` to use
the EXACT SAME key strings, with a `description/qty/total` example and an
explicit synonym ban (`item`, `quantity`, `amount`). Columns are declared
from the actual printed headers; rows copy those keys verbatim.

## Recovery — positional alignment (`_normalize_table_columns`)

After positional key normalization, each row is checked (count must match,
keys must differ from declared):

- **Tier 1 (zero overlap):** no cell key matches any declared key, e.g.
  `item/quantity/amount` vs `description/qty/total` → align by position,
  finding `"row {ri} aligned positionally: {orig} → {col_keys}"`.
- **Tier 2 (partial overlap, v1.3.0):** fewer than half the declared keys
  match (`matched < len(col_keys) // 2`) — majority mismatch signals
  synonym drift, e.g. only `description` of 5 receipt keys matches →
  align by position, finding `"row {ri} aligned positionally
  (partial-overlap, {n} of {total} keys matched): {orig} → {col_keys}"`.
- **Still structural errors:** count mismatch, or majority match
  (`matched >= len(col_keys) // 2`, e.g. `[a,b,c,x]` vs `[a,b,c,d]`) —
  attempting the declared schema and mostly getting it right but partly
  wrong is genuine partial data, never guessed into shape.
  `test_acceptance.py::test_wrong_row_and_invalid_structures_rejected`
  pins the 2-column case (`[a,c_unknown]` vs `[a,b]`: 1 ≥ 2//2 → reject).

Downstream cell evidence checks run unchanged on realigned rows.

The repair changes acceptance outcomes, so `ACCEPTANCE_POLICY_VERSION`
bumped `v1.1.0` → `v1.2.0` (Tier 1) → `v1.3.0` (Tier 2); the result-cache
fingerprint follows.

## Tests

`api/tests/test_table_column_alignment.py`: prompt phrase assertion,
zero-overlap 3-cell synonym row accepted with realigned keys + finding,
count-mismatch synonym row rejected, partial-overlap row rejected,
Tier-2 receipt row (1 of 5 keys) realigned with partial-overlap finding,
majority-match row (3 of 4) rejected, 4-cell vs 3-column row rejected.

## Related: OCR-split compound IDs (`collapse_ocr_spacing`)

Receipt IDs like `002043319-W` arrive from OCR as `0020433 19--W`
(digit-adjacent space plus a duplicated dash). `collapse_ocr_spacing()`
(`api/app/core/security.py`) now also collapses digit–hyphen-adjacent
spaces and hyphen runs (`-{2,}` → `-`), so
`value_in_text("002043319-W", "INV 0020433 19--W date 27-06-2018")` is
`True`. Letter word boundaries are untouched (`19W` still never matches).
Dot-adjacent spacing (`30 . 30` vs `30.30`) is deliberately NOT collapsed —
locked in `test_security.py` as current behavior. The same function backs
`evidence.py` span resolution, so block/page matching improves together.
Covered by `test_security.py::test_ocr_split_compound_id_matches` and
`test_collapse_handles_split_compound_id`.
