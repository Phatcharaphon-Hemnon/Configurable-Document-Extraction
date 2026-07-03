import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { recognize } from 'tesseract.js';

type DemoCriterion = {
  id: string;
  title: string;
  expected: string;
};

type RecommendedModel = {
  name: string;
  display_name: string;
  reason: string;
};

type ApiStatus = {
  name: string;
  status: string;
  frontend_origins: string[];
  supported_doc_types: string[];
  endpoints: string[];
  recommended_extraction_model?: RecommendedModel;
  demo_criteria?: DemoCriterion[];
};

type FileUploadMeta = {
  filename: string;
  content_type?: string | null;
  size_bytes?: number | null;
};

type ExtractionField = {
  value: string | number | boolean | null;
  confidence: number;
  source_span?: string | null;
};

type ValidationIssue = {
  field: string;
  message: string;
  severity?: string;
};

type ValidationResult = {
  is_valid: boolean;
  issues: ValidationIssue[];
};

type JudgeIssue = {
  field: string;
  message: string;
  severity?: string;
};

type JudgeResult = {
  score: number;
  issues: JudgeIssue[];
  notes: string;
};

type ExtractionResult = {
  id: string;
  request: FileUploadMeta;
  doc_type: string;
  language: string;
  routing_reason?: string | null;
  extracted_fields: Record<string, ExtractionField>;
  validation: ValidationResult;
  judge?: JudgeResult | null;
};

type EvaluateResponse = {
  score: number;
  precision: number;
  recall: number;
  f1: number;
  summary: string;
  mismatches: Array<Record<string, unknown>>;
};

type BatchResult = {
  fileName: string;
  sourceText: string;
  extraction: ExtractionResult;
  evaluation: EvaluateResponse;
};

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const defaultRecommendedModel: RecommendedModel = {
  name: 'gemini-3.1-pro-preview',
  display_name: 'Gemini 3.1 Pro Preview',
  reason:
    'Best quality choice from the available model list for mixed document extraction, validation-heavy batches, and judge review.',
};

const defaultDemoCriteria: DemoCriterion[] = [
  {
    id: 'mixed-batch',
    title: 'Upload mixed batch of 3 doc types',
    expected: 'One invoice, one PO, and one delivery note should be classified separately.',
  },
  {
    id: 'router-extraction',
    title: 'Show router classification + per-type extraction',
    expected: 'Each file should display its routed doc type, extracted fields, and confidence values.',
  },
  {
    id: 'bad-doc',
    title: 'Trigger intentional bad doc',
    expected: 'A malformed invoice should raise validation issues and judge warnings.',
  },
  {
    id: 'eval-dashboard',
    title: 'Show eval dashboard with F1 metrics',
    expected: 'Precision, recall, and F1 should be computed for the demo batch.',
  },
];

const expectedGroundTruth: Record<string, Record<string, string | number>> = {
  invoice: {
    invoice_number: 'INV-0001',
    total_amount: 1500,
    currency: 'USD',
  },
  po: {
    po_number: 'PO-1001',
    supplier_name: 'Example Supplier',
    order_date: '2026-06-30',
  },
  delivery_note: {
    delivery_note_number: 'DN-5001',
    delivered_by: 'Courier Service',
    delivery_date: '2026-06-30',
  },
};

function toPlainFields(fields: Record<string, ExtractionField>): Record<string, string | number | boolean | null> {
  return Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value])) as Record<
    string,
    string | number | boolean | null
  >;
}

function createDemoFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/plain' });
}

function isImageFile(file: File): boolean {
  return file.type.startsWith('image/') || /\.(png|jpg|jpeg|webp|gif|bmp|tif|tiff)$/i.test(file.name);
}

async function extractTextFromImage(file: File): Promise<string> {
  const result = await recognize(file, 'eng');
  return result.data.text.trim();
}

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [batchResults, setBatchResults] = useState<BatchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadedTemplates, setLoadedTemplates] = useState<number | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);

  useEffect(() => {
    const loadStatus = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/`);
        const payload = (await response.json()) as ApiStatus;
        setApiStatus(payload);
      } catch {
        setApiStatus(null);
      }
    };

    const loadTemplates = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/templates`);
        const payload = (await response.json()) as { templates?: unknown[] };
        setLoadedTemplates(Array.isArray(payload.templates) ? payload.templates.length : 0);
      } catch {
        setLoadedTemplates(null);
      }
    };

    void loadStatus();
    void loadTemplates();
  }, []);

  const supportedTypes = useMemo(() => apiStatus?.supported_doc_types ?? ['invoice', 'po', 'delivery_note'], [apiStatus]);
  const recommendedModel = apiStatus?.recommended_extraction_model ?? defaultRecommendedModel;
  const demoCriteria = apiStatus?.demo_criteria ?? defaultDemoCriteria;

  const aggregateMetrics = useMemo(() => {
    if (batchResults.length === 0) {
      return null;
    }

    const totals = batchResults.reduce(
      (accumulator, item) => {
        accumulator.precision += item.evaluation.precision;
        accumulator.recall += item.evaluation.recall;
        accumulator.f1 += item.evaluation.f1;
        accumulator.validCount += item.extraction.validation.is_valid ? 1 : 0;
        accumulator.judgeFlags += item.extraction.judge?.issues.length ?? 0;
        return accumulator;
      },
      { precision: 0, recall: 0, f1: 0, validCount: 0, judgeFlags: 0 },
    );

    const count = batchResults.length;
    return {
      precision: totals.precision / count,
      recall: totals.recall / count,
      f1: totals.f1 / count,
      validCount: totals.validCount,
      judgeFlags: totals.judgeFlags,
    };
  }, [batchResults]);

  const satisfiedCriteria = useMemo(() => {
    const docTypes = new Set(batchResults.map((item) => item.extraction.doc_type));
    const hasMixedBatch = ['invoice', 'po', 'delivery_note'].every((type) => docTypes.has(type));
    const hasRouterCoverage = batchResults.length > 0;
    const hasBadDoc = batchResults.some(
      (item) => !item.extraction.validation.is_valid || (item.extraction.judge?.issues.length ?? 0) > 0,
    );
    const hasEvaluationMetrics = batchResults.length > 0;

    return {
      mixedBatch: hasMixedBatch,
      routerExtraction: hasRouterCoverage,
      badDoc: hasBadDoc,
      evalDashboard: hasEvaluationMetrics,
    };
  }, [batchResults]);

  const resetBatch = () => {
    setSelectedFiles([]);
    setBatchResults([]);
    setError(null);
    setCopyMessage(null);
    setFileInputKey((currentKey) => currentKey + 1);
  };

  const loadDemoBatch = () => {
    setError(null);
    setBatchResults([]);
    setCopyMessage(null);
    setSelectedFiles([
      createDemoFile('invoice_mixed_batch.txt', 'Invoice No: INV-0001\nTotal: 1,500.00\nCurrency: USD'),
      createDemoFile('po_mixed_batch.txt', 'PO No: PO-1001\nSupplier: Example Supplier\nOrder Date: 2026-06-30'),
      createDemoFile('delivery_note_mixed_batch.txt', 'DN No: DN-5001\nDelivered By: Courier Service\nDelivery Date: 2026-06-30'),
    ]);
  };

  const loadBadDoc = () => {
    setError(null);
    setBatchResults([]);
    setCopyMessage(null);
    setSelectedFiles([
      createDemoFile(
        'bad_invoice_review.txt',
        'Invoice No: INV-0001\nTotal: -1500.00\nThis document is intentionally malformed for validation checks.',
      ),
    ]);
  };

  const handleFileSelection = (files: File[]) => {
    setSelectedFiles(files);
    setBatchResults([]);
    setError(null);
    setCopyMessage(null);
  };

  const copyBatchJson = async () => {
    if (batchResults.length === 0) {
      return;
    }

    await navigator.clipboard.writeText(JSON.stringify(batchResults, null, 2));
    setCopyMessage('JSON copied to clipboard.');
  };

  const submitDocuments = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (selectedFiles.length === 0) {
      setError('Choose one or more document files first.');
      return;
    }

    setBusy(true);
    setError(null);
    setBatchResults([]);

    try {
      const outputs: BatchResult[] = [];

      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append('file', file);

        let sourceText = await file.text();

        if (isImageFile(file)) {
          const ocrText = await extractTextFromImage(file);
          if (!ocrText) {
            throw new Error(`Could not read text from image file ${file.name}. Try a clearer image.`);
          }

          sourceText = ocrText;
          formData.append('ocr_text', ocrText);
        }

        const extractionResponse = await fetch(`${apiBaseUrl}/extract`, {
          method: 'POST',
          body: formData,
        });

        const extractionPayload = (await extractionResponse.json()) as ExtractionResult & { detail?: string };

        if (!extractionResponse.ok) {
          throw new Error(extractionPayload.detail || `Extraction failed for ${file.name}`);
        }

        const evaluationResponse = await fetch(`${apiBaseUrl}/evaluate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            doc_type: extractionPayload.doc_type,
            prediction: toPlainFields(extractionPayload.extracted_fields),
            ground_truth: expectedGroundTruth[extractionPayload.doc_type] ?? {},
            source_text: sourceText,
          }),
        });

        const evaluationPayload = (await evaluationResponse.json()) as EvaluateResponse & { detail?: string };

        if (!evaluationResponse.ok) {
          throw new Error(evaluationPayload.detail || `Evaluation failed for ${file.name}`);
        }

        outputs.push({
          fileName: file.name,
          sourceText,
          extraction: extractionPayload,
          evaluation: evaluationPayload,
        });
      }

      setBatchResults(outputs);
      setSelectedFiles([]);
      setFileInputKey((currentKey) => currentKey + 1);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Extraction failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="shell">
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">React frontend + FastAPI backend</p>
          <h1>Mixed-batch extraction with router, validator, judge, and F1 scoring.</h1>
          <p className="lede">
            Use the demo batch to show the three supported document types in one upload, then add a bad invoice to
            demonstrate validation and judge flags.
          </p>

          <div className="status-row">
            <span className={apiStatus ? 'pill success' : 'pill muted'}>
              {apiStatus ? `Backend: ${apiStatus.status}` : 'Backend status unavailable'}
            </span>
            <span className="pill muted">
              {loadedTemplates === null ? 'Templates unavailable' : `${loadedTemplates} template groups loaded`}
            </span>
            <span className="pill highlight">Model: {recommendedModel.display_name}</span>
          </div>
        </div>

        <aside className="panel side-panel">
          <h2>Recommended extraction model</h2>
          <div className="model-card">
            <p className="model-name">{recommendedModel.display_name}</p>
            <p className="model-reason">{recommendedModel.reason}</p>
          </div>
          <dl>
            <div>
              <dt>Base URL</dt>
              <dd>{apiBaseUrl}</dd>
            </div>
            <div>
              <dt>Supported</dt>
              <dd>{supportedTypes.join(', ')}</dd>
            </div>
            <div>
              <dt>Demo state</dt>
              <dd>{batchResults.length ? `${batchResults.length} files processed` : 'Waiting for demo batch'}</dd>
            </div>
          </dl>
        </aside>
      </section>

      <section className="criteria-grid">
        {demoCriteria.map((criterion) => {
          const status =
            criterion.id === 'mixed-batch'
              ? satisfiedCriteria.mixedBatch
              : criterion.id === 'router-extraction'
                ? satisfiedCriteria.routerExtraction
                : criterion.id === 'bad-doc'
                  ? satisfiedCriteria.badDoc
                  : satisfiedCriteria.evalDashboard;

          return (
            <article className={`panel criteria-card ${status ? 'done' : ''}`} key={criterion.id}>
              <div className="criteria-head">
                <span className="criteria-badge">{status ? 'Ready' : 'Pending'}</span>
                <h2>{criterion.title}</h2>
              </div>
              <p>{criterion.expected}</p>
            </article>
          );
        })}
      </section>

      <section className="content-grid">
        <form className="panel form-panel" onSubmit={submitDocuments}>
          <div className="section-head">
            <h2>Run the demo batch</h2>
            <p>Upload one or more files, or use the prebuilt mixed-batch examples below.</p>
          </div>

          <label className="upload-box">
            <input
              key={fileInputKey}
              type="file"
              multiple
              accept=".txt,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,image/*"
              onChange={(event) => handleFileSelection(Array.from(event.target.files ?? []))}
            />
            <span>
              {selectedFiles.length > 0
                ? `${selectedFiles.length} file${selectedFiles.length === 1 ? '' : 's'} selected`
                : 'Choose invoice, PO, delivery note, or image files'}
            </span>
          </label>

          <div className="button-row">
            <button type="button" className="secondary" onClick={loadDemoBatch}>
              Load mixed demo batch
            </button>
            <button type="button" className="secondary" onClick={loadBadDoc}>
              Load bad invoice
            </button>
            <button type="button" className="ghost" onClick={resetBatch}>
              Clear
            </button>
          </div>

          <div className="actions">
            <button type="submit" disabled={busy}>
              {busy ? 'Running extraction...' : 'Run extraction + eval'}
            </button>
            {error ? <p className="message error">{error}</p> : <p className="message">Ready to submit.</p>}
          </div>

          <div className="file-list">
            {selectedFiles.length === 0 ? (
              <p className="muted-text">No files selected yet.</p>
            ) : (
              selectedFiles.map((file) => <span key={file.name}>{file.name}</span>)
            )}
          </div>
        </form>

        <section className="panel result-panel">
          <div className="section-head">
            <h2>Document extraction</h2>
            <p>{batchResults.length > 0 ? 'Copy the extracted JSON from the current run.' : 'Run a file or image to populate the extraction data.'}</p>
          </div>

          {batchResults.length > 0 ? (
            <>
              <div className="dashboard-actions">
                <button type="button" className="secondary" onClick={copyBatchJson} disabled={batchResults.length === 0}>
                  Copy extraction JSON
                </button>
                {copyMessage ? <span className="copy-message">{copyMessage}</span> : null}
              </div>

              <pre>{JSON.stringify(batchResults, null, 2)}</pre>
            </>
          ) : (
            <div className="empty-state">Run a file or image to see the extracted data here.</div>
          )}
        </section>
      </section>

      <section className="panel extraction-summary">
        <div className="section-head">
          <h2>Evaluation dashboard</h2>
          <p>{apiStatus?.endpoints?.includes('/evaluate') ? 'Precision, recall, and F1 for the current batch' : 'Waiting for response'}</p>
        </div>

        {aggregateMetrics ? (
          <div className="metric-grid">
            <article className="metric-card">
              <span>Precision</span>
              <strong>{aggregateMetrics.precision.toFixed(2)}</strong>
            </article>
            <article className="metric-card">
              <span>Recall</span>
              <strong>{aggregateMetrics.recall.toFixed(2)}</strong>
            </article>
            <article className="metric-card accent">
              <span>F1</span>
              <strong>{aggregateMetrics.f1.toFixed(2)}</strong>
            </article>
            <article className="metric-card">
              <span>Valid docs</span>
              <strong>{aggregateMetrics.validCount}/{batchResults.length}</strong>
            </article>
          </div>
        ) : (
          <div className="empty-state">Run the mixed batch to populate the dashboard.</div>
        )}

        <div className="issue-summary">
          <span className="pill muted">Validation flags: {batchResults.filter((item) => !item.extraction.validation.is_valid).length}</span>
          <span className="pill muted">
            Judge issues: {batchResults.reduce((count, item) => count + (item.extraction.judge?.issues.length ?? 0), 0)}
          </span>
        </div>
      </section>

      <section className="results-grid">
        {batchResults.map((item) => {
          const fieldEntries = Object.entries(item.extraction.extracted_fields);
          const validationIssues = item.extraction.validation.issues;
          const judgeIssues = item.extraction.judge?.issues ?? [];

          return (
            <article className="panel result-card" key={`${item.fileName}-${item.extraction.id}`}>
              <div className="result-card-head">
                <div>
                  <p className="eyebrow">{item.fileName}</p>
                  <h2>{item.extraction.doc_type}</h2>
                </div>
                <div className="score-pill">F1 {item.evaluation.f1.toFixed(2)}</div>
              </div>

              <p className="routing-note">
                {item.extraction.routing_reason ?? 'Router classification available from the document type.'}
              </p>

              <div className="extraction-data">
                <h3>Extracted Data</h3>
                <pre className="data-output">
                  {JSON.stringify(
                    Object.fromEntries(
                      fieldEntries.map(([name, field]) => [name, field.value])
                    ),
                    null,
                    2
                  )}
                </pre>
                <button
                  className="copy-btn"
                  onClick={() => {
                    const dataOnly = Object.fromEntries(
                      fieldEntries.map(([name, field]) => [name, field.value])
                    );
                    navigator.clipboard.writeText(JSON.stringify(dataOnly, null, 2));
                  }}
                >
                  Copy Data
                </button>
              </div>

              <div className="status-grid">
                <article className={`status-box ${item.extraction.validation.is_valid ? 'success' : 'warning'}`}>
                  <h3>Validation</h3>
                  <p>{item.extraction.validation.is_valid ? 'Document passed validation.' : 'Validation issues detected.'}</p>
                  {validationIssues.length > 0 ? (
                    <ul>
                      {validationIssues.map((issue, index) => (
                        <li key={`${issue.field}-${index}`}>
                          <strong>{issue.field}</strong> {issue.message}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </article>

                <article className={`status-box ${judgeIssues.length > 0 ? 'warning' : 'success'}`}>
                  <h3>Judge</h3>
                  <p>{item.extraction.judge?.notes ?? 'No judge response available.'}</p>
                  {judgeIssues.length > 0 ? (
                    <ul>
                      {judgeIssues.map((issue, index) => (
                        <li key={`${issue.field}-${index}`}>
                          <strong>{issue.field}</strong> {issue.message}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </article>
              </div>

              <div className="evaluation-row">
                <span>Precision {item.evaluation.precision.toFixed(2)}</span>
                <span>Recall {item.evaluation.recall.toFixed(2)}</span>
                <span>Score {item.evaluation.score.toFixed(2)}</span>
              </div>
            </article>
          );
        })}
      </section>
    </main>
  );
}

export default App;