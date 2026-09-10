# Eval report — multilingual page extraction

Generated 2026-09-10T06:13:14.262237+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T06:13:14.262237+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | cc8302fb4979ba447a40fbf056f5fd62b8c7f44c |
| working_tree | uncommitted implementation |
| implementation_sha256 | 9f616efe2f39aa314ba881bef3380d0ff90f73aea545c95019bdfcda93f813b0 |
| source_hashes | {'THAI_RECEIPT.jpg': '450b970467019dcd2d4282c667e57e4d752f437f3242a09f782e9f4e807495f1', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_m12', 'eval_c3'] |
| report_scope | combined full run across two chunks (3h cap) |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 14 / 14 |
| Successful / failed pages | 13 / 1 |
| Field macro precision / recall / F1 | 0.444 / 0.460 / **0.445** |
| Successful-page-only F1 | 0.479 |
| Routing / language accuracy | 0.929 / 0.643 |
| Table cell accuracy | 0.312 |
| Table row / column coverage | 0.639 / 0.359 |
| Review rate | 64.3% |
| Median / p95 page seconds | 851.39 / 1472.37 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1472.37 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 947.04 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 381.27 | completed |
| ICR.png / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 73.05 | FAILED: OCR text incoherent: too fragmented for reliable extraction (rescan at higher DPI or review manually) |
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 518.28 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | flagged | 1180.82 | review |
| Invoice2.jpg / 1 | invoice → invoice | 0.071 | 0.100 | 0.083 | flagged | 1433.24 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 880.62 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | flagged | 1441.41 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.500 | 0.500 | 0.500 | unavailable | 921.44 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 822.15 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 624.16 | completed |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 373.49 | completed |
| purchase_orders_2.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 433.55 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 699.45 |
| invoice | 8 | 0.369 | 1063.93 |
| purchase_order | 4 | 0.714 | 407.41 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 11 | 0.459 | 624.16 |
| th | 3 | 0.393 | 921.44 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 4 | 0.400 | 1307.03 |
| .pdf | 7 | 0.602 | 624.16 |
| .png | 2 | 0.158 | 476.84 |
| .webp | 1 | 0.095 | 518.28 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 13 | 7650.48 | 0 | 34688 |
| judge | 8 | 2556.55 | 0 | 16303 |
| ocr | 14 | 69.10 | 0 | 0 |
| render | 14 | 11.43 | 0 | 0 |
| router | 14 | 1201.17 | 0 | 9584 |
| validator | 13 | 0.37 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 13.79s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.630 | 0.000000 | False |
| Delivery_note2.png | 1.550 | 0.000000 | False |
| ICR.png | 10.403 | 0.000000 | False |
| Invoice+purchase.pdf | 15.107 | 0.000000 | False |
| Invoice1.jpg | 5.299 | 0.000000 | False |
| Invoice2.jpg | 10.169 | 0.000000 | False |
| THAI_RECEIPT.jpg | 12.121 | 0.003286 | True |
| THAI_bill.jpg | 8.927 | 0.001188 | True |
| Thai(invoice)+EN(Purchase).pdf | 9.431 | 0.000911 | True |
| purchase_orders1.pdf | 1.979 | 0.000118 | True |
| purchase_orders_2.pdf | 1.913 | 0.000092 | True |

## Annotation and scoring limits

- Reference answers were visually transcribed by the assistant before evaluation; they have not been independently human-adjudicated.
- Missing/failed expected pages remain in metric denominators. Null cells and explicitly excluded fields are unscored. Extra fields count as false positives; extra rows/columns are recorded in metrics.json.
- Column matching uses exact normalized printed headers; no field or column synonym mapping. IDs preserve leading zeros. Source-language strings are retained.
- Some merchant/product names are Malay, German or Afrikaans inside English-labeled documents. Language labels describe the primary document labels.
- The blank Thai form and ambiguous handwriting are intentional review cases. Printed inconsistent totals are not corrected in gold answers.
- Source-grounded output can still contain OCR mistakes. A review-free result is not proof of accuracy. This small developer gold set is not an independent test benchmark.
- Timings describe this model/host/run; no speedup percentage is claimed without a comparable baseline.

## Agents and RAG sources

- Router; InvoiceExtractor, PurchaseOrderExtractor, DeliveryNoteExtractor; deterministic Validator; Judge (explicit skipped/unavailable outcomes).
- Local OCR runs before text-only agents. field_catalog/*.json supplies compact catalog context.
- few_shot/*/*.json and app/services/rag_retriever.py are available; few-shot defaults OFF (top-0). ground_truth/manifest.json is scoring-only and never prompt input.

## Reproduce

```bash
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
```

Pass bar: pipeline runs on sample inputs and real metrics are reported; final-demo accuracy thresholds are not required.

---

## Release subset (fixed files from manifest.release_subset)

Generated 2026-09-10T06:13:14.262237+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T06:13:14.262237+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | cc8302fb4979ba447a40fbf056f5fd62b8c7f44c |
| working_tree | uncommitted implementation |
| implementation_sha256 | 9f616efe2f39aa314ba881bef3380d0ff90f73aea545c95019bdfcda93f813b0 |
| source_hashes | {'THAI_RECEIPT.jpg': '450b970467019dcd2d4282c667e57e4d752f437f3242a09f782e9f4e807495f1', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_m12', 'eval_c3'] |
| report_scope | fixed release subset section inside this report |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 11 / 11 |
| Successful / failed pages | 11 / 0 |
| Field macro precision / recall / F1 | 0.498 / 0.516 / **0.498** |
| Successful-page-only F1 | 0.498 |
| Routing / language accuracy | 1.000 / 0.636 |
| Table cell accuracy | 0.286 |
| Table row / column coverage | 0.600 / 0.338 |
| Review rate | 63.6% |
| Median / p95 page seconds | 880.62 / 1472.37 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1472.37 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 947.04 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 381.27 | completed |
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 518.28 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | flagged | 1180.82 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 880.62 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | flagged | 1441.41 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.500 | 0.500 | 0.500 | unavailable | 921.44 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 822.15 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 624.16 | completed |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 373.49 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 699.45 |
| invoice | 6 | 0.479 | 1063.93 |
| purchase_order | 3 | 0.730 | 381.27 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 8 | 0.537 | 752.39 |
| th | 3 | 0.393 | 921.44 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 3 | 0.505 | 1180.82 |
| .pdf | 6 | 0.591 | 723.15 |
| .png | 1 | 0.316 | 880.62 |
| .webp | 1 | 0.095 | 518.28 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 11 | 6337.04 | 0 | 28841 |
| judge | 7 | 2172.82 | 0 | 13561 |
| ocr | 11 | 50.62 | 0 | 0 |
| render | 11 | 7.42 | 0 | 0 |
| router | 11 | 983.20 | 0 | 7646 |
| validator | 11 | 0.32 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 11.63s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.630 | 0.000000 | False |
| Delivery_note2.png | 1.550 | 0.000000 | False |
| Invoice+purchase.pdf | 15.107 | 0.000000 | False |
| Invoice1.jpg | 5.299 | 0.000000 | False |
| THAI_RECEIPT.jpg | 12.121 | 0.003286 | True |
| THAI_bill.jpg | 8.927 | 0.001188 | True |
| Thai(invoice)+EN(Purchase).pdf | 9.431 | 0.000911 | True |
| purchase_orders1.pdf | 1.979 | 0.000118 | True |

## Annotation and scoring limits

- Reference answers were visually transcribed by the assistant before evaluation; they have not been independently human-adjudicated.
- Missing/failed expected pages remain in metric denominators. Null cells and explicitly excluded fields are unscored. Extra fields count as false positives; extra rows/columns are recorded in metrics.json.
- Column matching uses exact normalized printed headers; no field or column synonym mapping. IDs preserve leading zeros. Source-language strings are retained.
- Some merchant/product names are Malay, German or Afrikaans inside English-labeled documents. Language labels describe the primary document labels.
- The blank Thai form and ambiguous handwriting are intentional review cases. Printed inconsistent totals are not corrected in gold answers.
- Source-grounded output can still contain OCR mistakes. A review-free result is not proof of accuracy. This small developer gold set is not an independent test benchmark.
- Timings describe this model/host/run; no speedup percentage is claimed without a comparable baseline.

## Agents and RAG sources

- Router; InvoiceExtractor, PurchaseOrderExtractor, DeliveryNoteExtractor; deterministic Validator; Judge (explicit skipped/unavailable outcomes).
- Local OCR runs before text-only agents. field_catalog/*.json supplies compact catalog context.
- few_shot/*/*.json and app/services/rag_retriever.py are available; few-shot defaults OFF (top-0). ground_truth/manifest.json is scoring-only and never prompt input.

## Reproduce

```bash
.venv/bin/python api/scripts/run_eval.py --all --gold-dir api/app/data/knowledge_base/ground_truth --output-dir .
```

Pass bar: pipeline runs on sample inputs and real metrics are reported; final-demo accuracy thresholds are not required.

