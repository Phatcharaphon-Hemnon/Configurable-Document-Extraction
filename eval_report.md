# Eval report — multilingual page extraction

Generated 2026-09-08T23:19:14.606928+00:00 · **LIVE pipeline**

## Run configuration

| Setting | Value |
|---|---|
| generated_at | 2026-09-08T23:19:14.606928+00:00 |
| provider | ollama-local |
| model | qwen2.5:1.5b |
| ocr | tesseract |
| ocr_languages | eng+tha |
| dpi | 300 |
| few_shot | 0 |
| concurrency | 1 |
| files | 11 |
| gold_sha256 | 5dcbf9c0d485427c95cb0a940ebeb59dbc59d29431ecdf13aeeb2073394c3b45 |
| revision | 99b8ae09e328f3e150371df3f09176f8f0620d76 |
| working_tree | uncommitted implementation |
| implementation_sha256 | 9a3b9d1e1a10d8e0a98566531e52caa07c2e3cc69881f0b9c79083669ecae757 |
| source_hashes | {'Delivery1.webp': '3f6f73f532e6c00a249c9d323dc4107aa30c8b49e2dae039a6c7765716330fd8', 'Delivery_note2.png': '09b12cab8e36e2205e3729eacdbe38e157b213905884a235e80e5572658c89ed', 'ICR.png': 'b42ff9d199aed8964ac4d0a4609903d462e2edb299b3ab190173c3d4147de19f', 'Invoice+purchase.pdf': 'f99e23a58301ac8b3c6e86e73052e9150c7bde8f3931f9a1b41d2c375e59221f', 'Invoice1.jpg': '69f0ac05640746713f1c346a4a6cdd2d01927ae328bcf34819a67833f99a71ad', 'Invoice2.jpg': 'dedc362a14f4dd64d59b93352ca9f7a51319af9457672f478d60a529a0854354', 'THAI_RECEIPT.jpg': '450b970467019dcd2d4282c667e57e4d752f437f3242a09f782e9f4e807495f1', 'THAI_bill.jpg': '6c554da9a7371fc418e1cf7fa53022169cf682a7b5da4f10b801b35fd4c82402', 'Thai(invoice)+EN(Purchase).pdf': '24b3e6e42b6fda6fa777d692279afd88bf48632015d73dec78946b0d56f1d271', 'purchase_orders1.pdf': '2d2e41d87b116938930e5d32fd734a25b4a48fb6de91d8f9557336582f5d7c61', 'purchase_orders_2.pdf': '7f64dabac88fbcf623b85de6051094bb3411f856b541cb914c5294216261d893'} |
| ocr_model_hashes | {'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2', 'tha.traineddata': '294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c'} |
| report_scope | fixed 8-file release subset (derived from full run) |

## End-to-end metrics (all expected pages, including failures)

| Metric | Value |
|---|---|
| Pages returned / expected | 11 / 11 |
| Successful / failed pages | 6 / 5 |
| Field macro precision / recall / F1 | 0.253 / 0.388 / **0.294** |
| Successful-page-only F1 | 0.539 |
| Routing / language accuracy | 1.000 / 0.455 |
| Table cell accuracy | 0.250 |
| Table row / column coverage | 0.400 / 0.231 |
| Review rate | 100.0% |
| Median / p95 page seconds | 413.87 / 558.50 |

## Per-page results

| File / page | Expected → predicted | P | R | F1 | Judge | Seconds | Outcome |
|---|---|---|---|---|---|---|---|
| Delivery1.webp / 1 | delivery_note → delivery_note | 0.100 | 0.083 | 0.091 | unavailable | 330.24 | review |
| Delivery_note2.png / 1 | delivery_note → delivery_note | 0.200 | 0.182 | 0.190 | flagged | 558.50 | review |
| Invoice+purchase.pdf / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 470.10 | FAILED: Extractor failed: LLM request timed out |
| Invoice+purchase.pdf / 2 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 460.90 | FAILED: Extractor failed: LLM request timed out |
| Invoice+purchase.pdf / 3 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | flagged | 283.63 | review |
| Invoice1.jpg / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 413.87 | FAILED: Extractor failed: LLM request timed out |
| THAI_RECEIPT.jpg / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 480.80 | FAILED: Extractor failed: LLM request timed out |
| THAI_bill.jpg / 1 | invoice → invoice | 0.000 | 0.000 | 0.000 | unavailable | 418.18 | FAILED: Extractor failed: LLM request timed out |
| Thai(invoice)+EN(Purchase).pdf / 1 | invoice → invoice | 0.235 | 1.000 | 0.381 | unavailable | 354.13 | review |
| Thai(invoice)+EN(Purchase).pdf / 2 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | flagged | 220.24 | review |
| purchase_orders1.pdf / 1 | purchase_order → purchase_order | 0.750 | 1.000 | 0.857 | flagged | 228.46 | review |

## Breakdown by expected

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| delivery_note | 2 | 0.141 | 444.37 |
| invoice | 6 | 0.063 | 439.54 |
| purchase_order | 3 | 0.857 | 228.46 |

## Breakdown by language

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| en | 8 | 0.357 | 372.05 |
| th | 3 | 0.127 | 418.18 |

## Breakdown by format

| Group | Pages | F1 | Median seconds |
|---|---|---|---|
| .jpg | 3 | 0.000 | 418.18 |
| .pdf | 6 | 0.492 | 318.88 |
| .png | 1 | 0.190 | 558.50 |
| .webp | 1 | 0.091 | 330.24 |

## Stage timing and provider usage

| Stage | Calls recorded | Total seconds | Retries | Reported tokens |
|---|---|---|---|---|
| extractor | 11 | 3163.27 | 6 | 12450 |
| judge | 6 | 671.33 | 0 | 4437 |
| ocr | 11 | 49.08 | 0 | 0 |
| render | 11 | 7.98 | 0 | 0 |
| router | 11 | 319.76 | 0 | 7671 |
| validator | 6 | 0.03 | 0 | 0 |

Token totals include only usage returned by the provider; failed attempts may have unreported usage. Stage totals are components of page latency, not additional latency.
Unattributed pipeline time: 7.59s (orchestration/tracing and, in older runs, failed stages without separate timing).

## OCR repeat measurements

Only OCR is repeated; no additional agent calls are made.

| File | First OCR + render seconds | Repeat seconds | Cache hit |
|---|---|---|---|
| Delivery1.webp | 3.165 | 0.000263 | True |
| Delivery_note2.png | 2.161 | 0.000166 | True |
| Invoice+purchase.pdf | 17.735 | 0.000552 | True |
| Invoice1.jpg | 5.267 | 0.000774 | True |
| THAI_RECEIPT.jpg | 11.347 | 0.001424 | True |
| THAI_bill.jpg | 7.720 | 0.000400 | True |
| Thai(invoice)+EN(Purchase).pdf | 7.603 | 0.000411 | True |
| purchase_orders1.pdf | 2.064 | 0.000137 | True |

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
