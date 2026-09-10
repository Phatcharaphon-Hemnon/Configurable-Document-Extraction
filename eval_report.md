# Eval report — multilingual page extraction

Generated 2026-09-10T01:06:56.107887+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T01:06:56.107887+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | 11b8d12df9e8f4c5056f6165dc1d0e405d425beb |
| working_tree | uncommitted implementation |
| implementation_sha256 | c86020ea0677b9c6e32b9230ab515d2d044de207448803bdc5dc174ecf84245f |
| source_hashes | {'ICR.png': 'b42ff9d199aed8964ac4d0a4609903d462e2edb299b3ab190173c3d4147de19f', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_rerun', 'eval_rerun2'] |
| report_scope | combined full run across two chunks (3h cap) |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 14 / 14 |
| Successful / failed pages | 13 / 1 |
| Field macro precision / recall / F1 | 0.447 / 0.460 / **0.446** |
| Successful-page-only F1 | 0.480 |
| Routing / language accuracy | 1.000 / 0.643 |
| Table cell accuracy | 0.312 |
| Table row / column coverage | 0.639 / 0.359 |
| Review rate | 64.3% |
| Median / p95 page seconds | 839.56 / 2088.19 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 524.22 | completed |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 875.57 | review |
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1506.90 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 978.72 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 259.71 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | flagged | 1212.57 | review |
| Invoice2.jpg / 1 | invoice → invoice | 0.071 | 0.100 | 0.083 | flagged | 1511.74 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | unavailable | 1170.65 | review |
| ICR.png / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 2088.19 | FAILED: Extractor failed: LLM request timed out |
| THAI_bill.jpg / 1 | invoice → invoice | 0.545 | 0.500 | 0.522 | flagged | 803.54 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 803.35 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 618.71 | completed |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 362.43 | completed |
| purchase_orders_2.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 444.46 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 699.90 |
| invoice | 8 | 0.372 | 1191.61 |
| purchase_order | 4 | 0.714 | 403.44 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 11 | 0.459 | 875.57 |
| th | 3 | 0.400 | 803.54 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 4 | 0.405 | 1191.61 |
| .pdf | 7 | 0.602 | 618.71 |
| .png | 2 | 0.158 | 1481.88 |
| .webp | 1 | 0.095 | 524.22 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 14 | 9484.83 | 1 | 34683 |
| judge | 8 | 2463.43 | 0 | 15287 |
| ocr | 14 | 70.10 | 0 | 0 |
| render | 14 | 12.57 | 0 | 0 |
| router | 14 | 1117.36 | 0 | 9595 |
| validator | 13 | 0.40 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 12.07s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 4.096 | 0.000000 | False |
| Delivery_note2.png | 2.016 | 0.000000 | False |
| ICR.png | 10.806 | 0.007109 | True |
| Invoice+purchase.pdf | 17.013 | 0.000000 | False |
| Invoice1.jpg | 6.170 | 0.000000 | False |
| Invoice2.jpg | 10.238 | 0.000000 | False |
| THAI_RECEIPT.jpg | 12.763 | 0.000000 | False |
| THAI_bill.jpg | 7.666 | 0.001100 | True |
| Thai(invoice)+EN(Purchase).pdf | 7.973 | 0.001024 | True |
| purchase_orders1.pdf | 1.893 | 0.000752 | True |
| purchase_orders_2.pdf | 2.040 | 0.000668 | True |

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

Generated 2026-09-10T01:06:56.107887+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-10T01:06:56.107887+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | ['Delivery1.webp', 'Delivery_note2.png', 'ICR.png', 'Invoice+purchase.pdf', 'Invoice1.jpg', 'Invoice2.jpg', 'THAI_RECEIPT.jpg', 'THAI_bill.jpg', 'Thai(invoice)+EN(Purchase).pdf', 'purchase_orders1.pdf', 'purchase_orders_2.pdf'] |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | 11b8d12df9e8f4c5056f6165dc1d0e405d425beb |
| working_tree | uncommitted implementation |
| implementation_sha256 | c86020ea0677b9c6e32b9230ab515d2d044de207448803bdc5dc174ecf84245f |
| source_hashes | {'ICR.png': 'b42ff9d199aed8964ac4d0a4609903d462e2edb299b3ab190173c3d4147de19f', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| combined_from | ['eval_rerun', 'eval_rerun2'] |
| report_scope | fixed release subset section inside this report |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 11 / 11 |
| Successful / failed pages | 11 / 0 |
| Field macro precision / recall / F1 | 0.502 / 0.516 / **0.500** |
| Successful-page-only F1 | 0.500 |
| Routing / language accuracy | 1.000 / 0.636 |
| Table cell accuracy | 0.286 |
| Table row / column coverage | 0.600 / 0.338 |
| Review rate | 63.6% |
| Median / p95 page seconds | 803.54 / 1506.90 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.111 | 0.083 | 0.095 | skipped | 524.22 | completed |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.375 | 0.273 | 0.316 | flagged | 875.57 | review |
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.467 | 0.389 | 0.424 | flagged | 1506.90 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.714 | 0.556 | 0.625 | flagged | 978.72 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 259.71 | completed |
| Invoice1.jpg / 1 | invoice → invoice | 0.667 | 0.625 | 0.645 | flagged | 1212.57 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.333 | 0.417 | 0.370 | unavailable | 1170.65 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.545 | 0.500 | 0.522 | flagged | 803.54 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.222 | 0.500 | 0.308 | flagged | 803.35 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | skipped | 618.71 | completed |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | skipped | 362.43 | completed |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.206 | 699.90 |
| invoice | 6 | 0.482 | 1074.69 |
| purchase_order | 3 | 0.730 | 362.43 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 8 | 0.537 | 747.14 |
| th | 3 | 0.400 | 803.54 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 3 | 0.512 | 1170.65 |
| .pdf | 6 | 0.591 | 711.03 |
| .png | 1 | 0.316 | 875.57 |
| .webp | 1 | 0.095 | 524.22 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 11 | 6104.80 | 0 | 28836 |
| judge | 7 | 2062.82 | 0 | 12545 |
| ocr | 11 | 51.51 | 0 | 0 |
| render | 11 | 8.08 | 0 | 0 |
| router | 11 | 878.48 | 0 | 7657 |
| validator | 11 | 0.35 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 10.35s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 4.096 | 0.000000 | False |
| Delivery_note2.png | 2.016 | 0.000000 | False |
| Invoice+purchase.pdf | 17.013 | 0.000000 | False |
| Invoice1.jpg | 6.170 | 0.000000 | False |
| THAI_RECEIPT.jpg | 12.763 | 0.000000 | False |
| THAI_bill.jpg | 7.666 | 0.001100 | True |
| Thai(invoice)+EN(Purchase).pdf | 7.973 | 0.001024 | True |
| purchase_orders1.pdf | 1.893 | 0.000752 | True |

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

