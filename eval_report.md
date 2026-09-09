# Eval report — multilingual page extraction

Generated 2026-09-09T04:04:33.242073+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-09T04:04:33.242073+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | 11 |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | 7a856e64bde0d6c8795b4d474dcbabc7ba200a87 |
| working_tree | uncommitted implementation |
| implementation_sha256 | 9a3b9d1e1a10d8e0a98566531e52caa07c2e3cc69881f0b9c79083669ecae757 |
| source_hashes | {'Delivery1.webp': '3f6f73f532e6c00a249c9d323dc4107aa30c8b49e2dae039a6c7765716330fd8', 'Delivery_note2.png': '09b12cab8e36e2205e3729eacdbe38e157b213905884a235e80e5572658c89ed', 'ICR.png': 'b42ff9d199aed8964ac4d0a4609903d462e2edb299b3ab190173c3d4147de19f', 'Invoice+purchase.pdf': 'f99e23a58301ac8b3c6e86e73052e9150c7bde8f3931f9a1b41d2c375e59221f', 'Invoice1.jpg': '69f0ac05640746713f1c346a4a6cdd2d01927ae328bcf34819a67833f99a71ad', 'Invoice2.jpg': 'dedc362a14f4dd64d59b93352ca9f7a51319af9457672f478d60a529a0854354', 'THAI_RECEIPT.jpg': '450b970467019dcd2d4282c667e57e4d752f437f3242a09f782e9f4e807495f1', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 14 / 14 |
| Successful / failed pages | 14 / 0 |
| Field macro precision / recall / F1 | 0.435 / 0.451 / **0.439** |
| Successful-page-only F1 | 0.439 |
| Routing / language accuracy | 1.000 / 0.714 |
| Table cell accuracy | 0.331 |
| Table row / column coverage | 0.667 / 0.359 |
| Review rate | 100.0% |
| Median / p95 page seconds | 775.30 / 1444.71 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.125 | 0.083 | 0.100 | flagged | 739.23 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.125 | 0.091 | 0.105 | unavailable | 702.56 | review |
| ICR.png / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | flagged | 564.54 | review |
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.600 | 0.500 | 0.545 | flagged | 1246.54 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.667 | 0.556 | 0.606 | flagged | 920.28 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | flagged | 482.03 | review |
| Invoice1.jpg / 1 | invoice → invoice | 0.529 | 0.562 | 0.545 | flagged | 871.63 | review |
| Invoice2.jpg / 1 | invoice → invoice | 0.071 | 0.100 | 0.083 | flagged | 1287.69 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.357 | 0.417 | 0.385 | flagged | 1444.71 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.462 | 0.500 | 0.480 | flagged | 959.56 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.400 | 0.500 | 0.444 | flagged | 563.49 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | flagged | 461.49 | review |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | flagged | 471.39 | review |
| purchase_orders_2.pdf / 1 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | unavailable | 811.37 | review |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.103 | 720.90 |
| invoice | 8 | 0.386 | 939.92 |
| purchase_order | 4 | 0.714 | 476.71 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 11 | 0.440 | 739.23 |
| th | 3 | 0.436 | 959.56 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 4 | 0.373 | 1123.63 |
| .pdf | 7 | 0.636 | 563.49 |
| .png | 2 | 0.053 | 633.55 |
| .webp | 1 | 0.100 | 739.23 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 14 | 7135.33 | 0 | 33598 |
| judge | 14 | 3089.87 | 0 | 19424 |
| ocr | 14 | 71.13 | 0 | 0 |
| render | 14 | 12.07 | 0 | 0 |
| router | 14 | 1203.85 | 0 | 9581 |
| validator | 14 | 0.09 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 14.18s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.432 | 0.002728 | True |
| Delivery_note2.png | 1.900 | 0.001364 | True |
| ICR.png | 12.283 | 0.008214 | True |
| Invoice+purchase.pdf | 19.137 | 0.002218 | True |
| Invoice1.jpg | 5.359 | 0.002366 | True |
| Invoice2.jpg | 9.427 | 0.001763 | True |
| THAI_RECEIPT.jpg | 11.795 | 0.003606 | True |
| THAI_bill.jpg | 6.917 | 0.001562 | True |
| Thai(invoice)+EN(Purchase).pdf | 8.312 | 0.001260 | True |
| purchase_orders1.pdf | 2.148 | 0.000942 | True |
| purchase_orders_2.pdf | 2.491 | 0.000466 | True |

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

Generated 2026-09-09T04:04:33.242073+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-09T04:04:33.242073+00:00 |
| provider | ollama-local |
| model | qwen2.5:3b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | 11 |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | 7a856e64bde0d6c8795b4d474dcbabc7ba200a87 |
| working_tree | uncommitted implementation |
| implementation_sha256 | 9a3b9d1e1a10d8e0a98566531e52caa07c2e3cc69881f0b9c79083669ecae757 |
| source_hashes | {'Delivery1.webp': '3f6f73f532e6c00a249c9d323dc4107aa30c8b49e2dae039a6c7765716330fd8', 'Delivery_note2.png': '09b12cab8e36e2205e3729eacdbe38e157b213905884a235e80e5572658c89ed', 'ICR.png': 'b42ff9d199aed8964ac4d0a4609903d462e2edb299b3ab190173c3d4147de19f', 'Invoice+purchase.pdf': 'f99e23a58301ac8b3c6e86e73052e9150c7bde8f3931f9a1b41d2c375e59221f', 'Invoice1.jpg': '69f0ac05640746713f1c346a4a6cdd2d01927ae328bcf34819a67833f99a71ad', 'Invoice2.jpg': 'dedc362a14f4dd64d59b93352ca9f7a51319af9457672f478d60a529a0854354', 'THAI_RECEIPT.jpg': '450b970467019dcd2d4282c667e57e4d752f437f3242a09f782e9f4e807495f1', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| report_scope | fixed release subset section inside this report |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 11 / 11 |
| Successful / failed pages | 11 / 0 |
| Field macro precision / recall / F1 | 0.486 / 0.504 / **0.491** |
| Successful-page-only F1 | 0.491 |
| Routing / language accuracy | 1.000 / 0.727 |
| Table cell accuracy | 0.312 |
| Table row / column coverage | 0.640 / 0.338 |
| Review rate | 100.0% |
| Median / p95 page seconds | 739.23 / 1444.71 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.125 | 0.083 | 0.100 | flagged | 739.23 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.125 | 0.091 | 0.105 | unavailable | 702.56 | review |
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.600 | 0.500 | 0.545 | flagged | 1246.54 | review |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.667 | 0.556 | 0.606 | flagged | 920.28 | review |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | flagged | 482.03 | review |
| Invoice1.jpg / 1 | invoice → invoice | 0.529 | 0.562 | 0.545 | flagged | 871.63 | review |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.357 | 0.417 | 0.385 | flagged | 1444.71 | review |
| THAI_bill.jpg / 1 | invoice → invoice | 0.462 | 0.500 | 0.480 | flagged | 959.56 | review |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.400 | 0.500 | 0.444 | flagged | 563.49 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.667 | 0.667 | 0.667 | flagged | 461.49 | review |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | flagged | 471.39 | review |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.103 | 720.90 |
| invoice | 6 | 0.501 | 939.92 |
| purchase_order | 3 | 0.730 | 471.39 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 8 | 0.512 | 720.90 |
| th | 3 | 0.436 | 959.56 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 3 | 0.470 | 959.56 |
| .pdf | 6 | 0.631 | 522.76 |
| .png | 1 | 0.105 | 702.56 |
| .webp | 1 | 0.100 | 739.23 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 11 | 5399.97 | 0 | 25999 |
| judge | 11 | 2449.09 | 0 | 15431 |
| ocr | 11 | 51.52 | 0 | 0 |
| render | 11 | 7.48 | 0 | 0 |
| router | 11 | 944.05 | 0 | 7644 |
| validator | 11 | 0.07 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 10.73s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.432 | 0.002728 | True |
| Delivery_note2.png | 1.900 | 0.001364 | True |
| Invoice+purchase.pdf | 19.137 | 0.002218 | True |
| Invoice1.jpg | 5.359 | 0.002366 | True |
| THAI_RECEIPT.jpg | 11.795 | 0.003606 | True |
| THAI_bill.jpg | 6.917 | 0.001562 | True |
| Thai(invoice)+EN(Purchase).pdf | 8.312 | 0.001260 | True |
| purchase_orders1.pdf | 2.148 | 0.000942 | True |

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

_Run note: `purchase_orders_2.pdf` hit a transient `APIConnectionError` (no LLM
response, 153s in) during the full run; it was rescored once via
`run_eval.py --subset purchase_orders_2.pdf` (F1=0.667) and merged into the
metrics above with the script's own `summarize`/`build_combined_report`
helpers. Final: 14/14 pages, 0 failed._
