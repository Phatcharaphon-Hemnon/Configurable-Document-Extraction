# Catalog seeding (runtime knowledge base)

The API reads its field catalog from `KNOWLEDGE_BASE_PATH`
(default `api/data-local/knowledge_base`, untracked runtime state — **not**
the repo-tracked `api/app/data/knowledge_base`). If the runtime catalog is
missing or empty for a doc type, the pipeline degrades silently:

- extractor prompts fall back to "(catalog empty — extract clearly
  labeled fields)" (unguided LLM output),
- required-field / type checks are vacuous (nothing to check against),
- `catalog_label_set` is empty (field-name guard and label-as-value
  detection weakened),
- required coverage is `1.0` with zero accepted fields.

Observed 2026-09-18: the runtime KB held a single non-required
`invoice_number` (auto-discovered) and no PO/DN catalogs, which masked all
required-field errors and inflated coverage to 100% on a fully rejected
page (X51008042779.jpg).

## Seeding

Copy the canonical catalogs (merge-preserving: keep runtime
`ai_discovered` fields not present in canonical):

```bash
cp api/app/data/knowledge_base/field_catalog/*.json \
   api/data-local/knowledge_base/field_catalog/
```

Verify:

```bash
python3 -c "
from pathlib import Path
from app.services.field_catalog import FieldCatalog
import sys; sys.path.insert(0, 'api')
cat = FieldCatalog(Path('api/data-local/knowledge_base'))
for dt in ('invoice', 'purchase_order', 'delivery_note'):
    fs = cat.get_fields(dt)
    print(dt, len(fs), 'fields,', sum(f.required for f in fs), 'required')
"
```

Expected: invoice 17/4, purchase_order 12/2, delivery_note 10/2.

## Guard

`DocumentExtractionService.__init__` logs a loud warning per doc type with
an empty catalog (never raises — empty is tolerated by design, but no
longer silent). If you see
`Knowledge base catalog for X is EMPTY`, seed as above and restart the API
process (the catalog is read at startup; `FieldCatalog` also invalidates
its cache on file mtime change).
