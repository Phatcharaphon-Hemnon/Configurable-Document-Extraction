# Eval report — multilingual page extraction

Generated 2026-09-10T13:05:39.034630+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T13:05:39.034630+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | d9319b1a81ebf847f04e4f77d8160b2e3b275c66 |
| working_tree | uncommitted implementation |
| implementation_sha256 | 4133d5b28d6f3c14e4d69cefd610279d1d21e311c25e0927f1a92f8411340305 |
| source_hashes | {'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_m123', 'eval_c4'] |
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
| Review rate | 57.1% |
| Median / p95 page seconds | 731.11 / 1465.31 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1460.70 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 948.82 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 375.32 | completed |
| ICR.png / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 74.74 | FAILED: OCR text incoherent: too fragmented for reliable extraction (rescan at higher DPI or review manually) |
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 516.54 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | skipped | 847.25 | completed |
| Invoice2.jpg / 1 | invoice → invoice | 0.071 | 0.100 | 0.083 | flagged | 1454.72 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 894.58 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | flagged | 1465.31 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.500 | 0.500 | 0.500 | flagged | 1052.61 | review |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 390.26 | completed |
| purchase_orders_2.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 425.45 | completed |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 614.97 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 239.93 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 705.56 |
| invoice | 8 | 0.369 | 1000.71 |
| purchase_order | 4 | 0.714 | 382.79 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 11 | 0.459 | 516.54 |
| th | 3 | 0.393 | 1052.61 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 4 | 0.400 | 1253.67 |
| .pdf | 7 | 0.602 | 425.45 |
| .png | 2 | 0.158 | 484.66 |
| .webp | 1 | 0.095 | 516.54 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 13 | 7209.93 | 0 | 34688 |
| judge | 7 | 2292.56 | 0 | 16027 |
| ocr | 14 | 67.44 | 0 | 0 |
| render | 14 | 11.66 | 0 | 0 |
| router | 14 | 1166.39 | 0 | 9584 |
| validator | 13 | 0.31 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 12.91s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.421 | 0.000000 | False |
| Delivery_note2.png | 1.912 | 0.000000 | False |
| ICR.png | 10.188 | 0.000000 | False |
| Invoice+purchase.pdf | 14.905 | 0.000000 | False |
| Invoice1.jpg | 5.510 | 0.000000 | False |
| Invoice2.jpg | 10.940 | 0.000000 | False |
| THAI_RECEIPT.jpg | 11.730 | 0.000000 | False |
| THAI_bill.jpg | 8.172 | 0.000000 | False |
| Thai(invoice)+EN(Purchase).pdf | 7.846 | 0.001833 | True |
| purchase_orders1.pdf | 2.374 | 0.000000 | False |
| purchase_orders_2.pdf | 2.101 | 0.000000 | False |

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

Generated 2026-09-10T13:05:39.034630+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T13:05:39.034630+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | d9319b1a81ebf847f04e4f77d8160b2e3b275c66 |
| working_tree | uncommitted implementation |
| implementation_sha256 | 4133d5b28d6f3c14e4d69cefd610279d1d21e311c25e0927f1a92f8411340305 |
| source_hashes | {'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_m123', 'eval_c4'] |
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
| Review rate | 54.5% |
| Median / p95 page seconds | 847.25 / 1465.31 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1460.70 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 948.82 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 375.32 | completed |
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 516.54 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | skipped | 847.25 | completed |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 894.58 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | flagged | 1465.31 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.500 | 0.500 | 0.500 | flagged | 1052.61 | review |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 390.26 | completed |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 614.97 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 239.93 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 705.56 |
| invoice | 6 | 0.479 | 1000.71 |
| purchase_order | 3 | 0.730 | 375.32 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 8 | 0.537 | 681.89 |
| th | 3 | 0.393 | 1052.61 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 3 | 0.505 | 1052.61 |
| .pdf | 6 | 0.591 | 502.61 |
| .png | 1 | 0.316 | 894.58 |
| .webp | 1 | 0.095 | 516.54 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 11 | 5896.73 | 0 | 28841 |
| judge | 6 | 1900.15 | 0 | 13285 |
| ocr | 11 | 48.34 | 0 | 0 |
| render | 11 | 7.53 | 0 | 0 |
| router | 11 | 942.32 | 0 | 7646 |
| validator | 11 | 0.27 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 10.94s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.421 | 0.000000 | False |
| Delivery_note2.png | 1.912 | 0.000000 | False |
| Invoice+purchase.pdf | 14.905 | 0.000000 | False |
| Invoice1.jpg | 5.510 | 0.000000 | False |
| THAI_RECEIPT.jpg | 11.730 | 0.000000 | False |
| THAI_bill.jpg | 8.172 | 0.000000 | False |
| Thai(invoice)+EN(Purchase).pdf | 7.846 | 0.001833 | True |
| purchase_orders1.pdf | 2.374 | 0.000000 | False |

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

