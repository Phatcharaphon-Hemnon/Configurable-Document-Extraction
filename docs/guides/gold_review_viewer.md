# Gold review viewer guide (offline, local only)

Artifacts (all under `api/app/data/knowledge_base/ground_truth/`):

- `review_package_funsd_sroie.json` — authoritative review artifact (20 page
  entries: 10 SROIE + 10 FUNSD,
  one per SROIE document, with dataset provenance, original-vs-mapped labels,
  annotation scope, unsupported date scope, and extraction-only flags).
  Every entry `human_status: "pending"`,
  `annotator: "AI-assisted/provisional"`, `reviewer: null`. AI findings are
  recorded in `ai_audit_finding` and are NOT human verification.
- `review_viewer.html` — static viewer with dataset filter. No network,
  analytics, model calls, or manifest writes.

Why `.json`/`.html`/`.box.txt`/`.entities.json` are safe here: `run_eval.py
--all` scans only image/PDF suffixes (`FORMATS`); annotation and review files
are never picked up as gold inputs. Boxes/transcripts are review/eval-only
and are never fed into production extraction or OCR caches. No preview
bytes are generated (open the original source file beside the viewer instead),
so nothing new enters document-input discovery.

## Open

```bash
python3 -m http.server 8001
# browse to http://localhost:8001/api/app/data/knowledge_base/ground_truth/review_viewer.html
```

(`file://` fetch is not assumed to work; use localhost. Alternatively use the
file picker — no fetch needed since you load the JSON manually.)

## Review loop

1. Load `review_package_sroie.json` via the file picker.
2. Open each `sroie_*.jpg` beside the viewer; compare against the mapped
   fields and the preserved `.box.txt` / `.entities.json` (all referenced
   images exist in the same directory).
3. For each entry choose `pending | disputed | verified | unresolved`, add a note.
4. Enter reviewer identity (self-asserted; the export records an attestation,
   it does not authenticate identity).
5. Export decisions — a NEW file. The manifest is never overwritten.
6. (Optional) Re-load a prior decisions export to merge; the viewer validates
   known ref IDs, source hashes, policy version, allowed states, and duplicates,
   and shows conflicts before applying.

## Review focus (SROIE suite)

1. Confirm the 3-field production scope per page; `sroie_receipt_date` stays
   unsupported (never FN).
2. Confirm extraction-only status (router N/A) for receipt-only sources.
3. Native check of company/address transcriptions against images where the
   print is degraded (`X51006857265` date line is garbled in print; the
   entities JSON is the authoritative annotation).
4. Note `X51005301667` is a CREDIT NOTE, not an invoice (scope unaffected).
