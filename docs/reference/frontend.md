# Frontend

React 19 + TypeScript + Vite under `web/`. Palette is restricted to the
project colors: `#F2F2F2` page, `#CBCBCB` surfaces, `#174D38` primary,
`#4D1717` danger.

## Theming

Light + dark mode via CSS custom properties (`:root` / `[data-theme='dark']`).
Dark surfaces are derived by mixing `#174D38` toward black; the toggle lives in
the top bar and persists to `localStorage` (defaults to OS preference).

## Structure

```
web/src/
├── api/client.ts          # fetch layer (base URL from VITE_API_BASE_URL, MUST include /api)
├── types/extraction.ts    # mirrors backend FileExtractionResponse contracts
├── hooks/
│   ├── useDocumentQueue.ts  # upload groups: queued → uploading → done/error
│   ├── useEvaluation.ts     # per-document ground-truth evaluation state
│   └── useRecommendedModel.ts
├── components/
│   ├── Sidebar.tsx          # dropzone + document queue
│   ├── ExtractionTab.tsx    # fields table, completeness, judge review, copy/save JSON
│   ├── EvaluationTab.tsx    # ground-truth editor + score cards + mismatches
│   ├── PipelineStepper.tsx  # Router→Extractor→Validator→Judge progress
│   └── icons.tsx
├── utils/pipeline.ts       # stage computation, value formatting (pure)
├── App.tsx                 # composition root
└── styles.css              # design tokens + all styling (no inline styles)
```

## Behaviour

- **Upload**: files dropped/selected together form one group ("pages of one
  document"). Each group POSTs to `/api/extract`.
- **Multi-page**: a response with several `documents` shows page tabs; one
  result per page (multi-document PDFs supported).
- **Extraction tab**: field table (name, value, confidence bar, source span),
  `new` tag for AI-discovered fields, needs-review callout listing
  `validation_errors`, judge review card.
- **Evaluation tab**: paste/upload ground-truth JSON → `POST /api/evaluate` →
  Score/Precision/Recall/F1 cards + mismatch table. Prefill from extracted
  fields or copy raw JSON export.

## Env

`web/.env` → `VITE_API_BASE_URL=http://localhost:8000/api` (the `/api`
suffix is required).

## Page and history regression

`web/tests/pageResults.spec.ts` stubs API responses to verify the eight Thai table
columns, mixed-PDF page source switching and read-only history. Run
`cd web && npm run test:browser` after provisioning Chromium with
`npm run browser:install`. Browser tools store their dependencies inside the project.
The Vite server in Playwright configuration is test infrastructure; normal application
startup remains `scripts/run_all.sh`.
