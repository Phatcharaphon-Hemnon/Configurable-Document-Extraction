import { useEffect, useState, useRef, type ChangeEvent } from 'react';
import { recognize } from 'tesseract.js';

/* -------------------------------------------------------------------------- */
/*                                 Type Defs                                  */
/* -------------------------------------------------------------------------- */

type DocumentStatus = 'queued' | 'classifying' | 'extracting' | 'validating' | 'done' | 'flagged';

type ExtractionField = { value: unknown; confidence: number; source_span?: string | null };

type ExtractionResult = {
  id: string;
  doc_type: string | null;
  language: string | null;
  routing_reason?: string | null;
  extracted_fields: Record<string, ExtractionField>;
  validation: { is_valid: boolean; issues: Array<{ field: string; message: string }> };
  judge?: { score: number; issues: Array<{ field: string; message: string }>; notes: string } | null;
  needs_review?: boolean;
  error?: string | null;
};

type DocumentInQueue = {
  id: string;
  file: File;
  status: DocumentStatus;
  extractionResult?: ExtractionResult;
  dataOnly?: Record<string, unknown>;
  error?: string;
};

type ApiRoot = {
  recommended_extraction_model?: { display_name: string; reason: string };
};

/* -------------------------------------------------------------------------- */
/*                                 Constants                                  */
/* -------------------------------------------------------------------------- */

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';
const PIPELINE_STEPS = ['Router', 'Extractor', 'Validator', 'Judge'];

/* -------------------------------------------------------------------------- */
/*                                  Utilities                                 */
/* -------------------------------------------------------------------------- */

function isImageFile(file: File) {
  return file.type.startsWith('image/') || /\.(png|jpg|jpeg|webp|gif|bmp|tif|tiff)$/i.test(file.name);
}

async function ocrImage(file: File): Promise<string> {
  const result = await recognize(file, 'eng');
  return result.data.text.trim();
}

/* -------------------------------------------------------------------------- */
/*                                  Components                                */
/* -------------------------------------------------------------------------- */

const UploadIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);

const AlertTriangleIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

/* -------------------------------------------------------------------------- */
/*                                   App Main                                 */
/* -------------------------------------------------------------------------- */

function App() {
  const [modelName, setModelName] = useState<string>('Model');
  const [documentQueue, setDocumentQueue] = useState<DocumentInQueue[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [currentTab, setCurrentTab] = useState<'extraction' | 'evaluation'>('extraction');
  const [retrying, setRetrying] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${apiBaseUrl}/`)
      .then((r) => r.json())
      .then((d: ApiRoot) => {
        if (d?.recommended_extraction_model?.display_name) setModelName(d.recommended_extraction_model.display_name);
      })
      .catch(() => undefined);
  }, []);

  const addDocuments = (files: File[]) => {
    if (files.length === 0) return;
    const newDocuments: DocumentInQueue[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      status: 'queued',
    }));
    setDocumentQueue((prev) => [...prev, ...newDocuments]);
    setSelectedDocumentId((current) => current ?? newDocuments[0].id);
    processDocuments(newDocuments);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    addDocuments(files);
    event.target.value = '';
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const files = Array.from(event.dataTransfer.files);
    addDocuments(files);
  };

  const retryDocument = async (docId: string) => {
    const doc = documentQueue.find((d) => d.id === docId);
    if (!doc) return;
    setRetrying(docId);
    setDocumentQueue((prev) => prev.map((d) => d.id === docId ? { ...d, status: 'queued', error: undefined, extractionResult: undefined, dataOnly: undefined } : d));
    await processDocuments([doc]);
    setRetrying(null);
  };

  const processDocuments = async (documentsToProcess: DocumentInQueue[]) => {
    for (const doc of documentsToProcess) {
      const setStatus = (status: DocumentStatus) =>
        setDocumentQueue((prev) => prev.map((d) => (d.id === doc.id ? { ...d, status } : d)));

      setStatus('classifying');

      try {
        const form = new FormData();
        form.append('file', doc.file);

        if (isImageFile(doc.file)) {
          const ocrText = await ocrImage(doc.file);
          if (!ocrText) throw new Error(`OCR failed: ${doc.file.name}`);
          form.append('ocr_text', ocrText);
        }

        setStatus('extracting');
        const res = await fetch(`${apiBaseUrl}/extract`, { method: 'POST', body: form });
        const payload = (await res.json()) as ExtractionResult & { detail?: string };

        if (!res.ok) {
          throw new Error(payload.detail ?? `Extract failed for ${doc.file.name}`);
        }

        if (payload.error) {
          throw new Error(payload.error);
        }

        setStatus('validating');

        const dataOnly: Record<string, unknown> = Object.fromEntries(
          Object.entries(payload.extracted_fields).map(([k, v]) => [k, v.value]),
        );

        const finalStatus: DocumentStatus = payload.validation.is_valid ? 'done' : 'flagged';

        setDocumentQueue((prev) =>
          prev.map((d) => (d.id === doc.id ? { ...d, status: finalStatus, extractionResult: payload, dataOnly } : d)),
        );
      } catch (err) {
        console.error(`Error processing ${doc.file.name}:`, err);
        setDocumentQueue((prev) =>
          prev.map((d) =>
            d.id === doc.id ? { ...d, status: 'flagged', error: err instanceof Error ? err.message : 'Unknown error' } : d,
          ),
        );
      }
    }
  };

  const selectedDocument = documentQueue.find((doc) => doc.id === selectedDocumentId);
  const extractedFields = selectedDocument?.extractionResult?.extracted_fields;
  const validationErrors = selectedDocument?.extractionResult?.validation.issues;

  const currentPipelineStage = (doc: DocumentInQueue) => {
    switch (doc.status) {
      case 'queued': return 0;
      case 'classifying': return 1;
      case 'extracting': return 2;
      case 'validating': return 3;
      case 'done': return 4;
      case 'flagged': return doc.error ? 2 : 4; // error at extraction vs validation failure
      default: return 0;
    }
  };

  // Aggregate judge scores across all completed docs for session-level evaluation
  const judgedDocs = documentQueue.filter((d) => d.extractionResult?.judge);
  const evaluationMetrics = judgedDocs.length > 0
    ? {
        f1: `${(judgedDocs.reduce((sum, d) => sum + (d.extractionResult!.judge!.score), 0) / judgedDocs.length * 100).toFixed(0)}%`,
        judgedCount: judgedDocs.length,
      }
    : null;

  return (
    <>
      <div className="sidebar panel">
        <h2 className="panel-title">
          Document Queue <span className="document-count">({documentQueue.length})</span>
        </h2>
        <div
          className="file-upload-container"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
          onDrop={handleDrop}
        >
          <input
            type="file"
            multiple
            accept=".txt,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,image/*"
            onChange={handleFileChange}
            ref={fileInputRef}
            className="file-upload-input"
          />
          <UploadIcon />
          <p>Drag & drop or click to upload files</p>
        </div>

        <ul className="document-list">
          {documentQueue.length === 0 ? (
            <li className="empty-list">No documents uploaded yet.</li>
          ) : (
            documentQueue.map((doc) => (
              <li
                key={doc.id}
                className={`document-list-item ${doc.id === selectedDocumentId ? 'active' : ''}`}
                onClick={() => { setSelectedDocumentId(doc.id); setCurrentTab('extraction'); }}
              >
                <div className="queue-item-info">
                  <span className="file-name">{doc.file.name}</span>
                  {doc.extractionResult?.doc_type && (
                    <span className="doc-type-badge">{doc.extractionResult.doc_type}</span>
                  )}
                </div>
                <span className={`status-indicator ${doc.status}`} />
              </li>
            ))
          )}
        </ul>
      </div>

      <div className="main-content">
        <div className="panel">
          <h1 className="console-title">
            Extraction Console <span className="model-name">({modelName})</span>
          </h1>

          <div className="tabs">
            <button
              className={currentTab === 'extraction' ? 'active' : ''}
              onClick={() => setCurrentTab('extraction')}
            >
              Extraction
            </button>
            <button
              className={currentTab === 'evaluation' ? 'active' : ''}
              onClick={() => setCurrentTab('evaluation')}
            >
              Evaluation
            </button>
          </div>

          {currentTab === 'extraction' && (
            <>
              {!selectedDocument ? (
                <div className="empty-state">Select a document from the queue to view its extraction details.</div>
              ) : (
                <>
                  <div className="stepper">
                    {PIPELINE_STEPS.map((step, index) => {
                      const stage = currentPipelineStage(selectedDocument);
                      const isActive = index === stage;
                      const isCompleted = index < stage;
                      return (
                        <div key={step} className={`step ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}`}>
                          <div className="step-circle">{index + 1}</div>
                          <div className="step-label">{step}</div>
                        </div>
                      );
                    })}
                  </div>

                  {selectedDocument.status === 'flagged' && (validationErrors?.length || selectedDocument.error) && (
                    <div className="warning-box">
                      <span className="icon"><AlertTriangleIcon /></span>
                      <div style={{ flex: 1 }}>
                        <h3>Document Flagged for Review</h3>
                        {validationErrors && validationErrors.length > 0 && (
                          <>
                            <p>The following validation errors were found:</p>
                            <ul>
                              {validationErrors.map((error, index) => (
                                <li key={index}><strong>{error.field}:</strong> {error.message}</li>
                              ))}
                            </ul>
                          </>
                        )}
                        {selectedDocument.error && (
                          <>
                            <p>Processing Error:</p>
                            <div className="error-message">{selectedDocument.error}</div>
                          </>
                        )}
                        <button
                          className="btn-retry"
                          disabled={retrying === selectedDocument.id}
                          onClick={() => retryDocument(selectedDocument.id)}
                        >
                          {retrying === selectedDocument.id ? 'Retrying…' : '↺ Retry'}
                        </button>
                      </div>
                    </div>
                  )}

                  {selectedDocument.status === 'done' || selectedDocument.status === 'flagged' ? (
                    <>
                      <h3 className="section-title">Extracted Fields</h3>
                      <div className="table-wrapper">
                        <table className="extracted-fields-table">
                          <thead>
                            <tr>
                              <th>Field Name</th>
                              <th>Value</th>
                              <th>Confidence</th>
                              <th>Source Span</th>
                            </tr>
                          </thead>
                          <tbody>
                            {extractedFields && Object.keys(extractedFields).length > 0
                              ? Object.entries(extractedFields).map(([key, field]) => (
                                <tr key={key}>
                                  <td className="field-name">{key}</td>
                                  <td className="field-value">{String(field.value ?? '—')}</td>
                                  <td>
                                    <div className="confidence-cell">
                                      <div className="confidence-bar-container">
                                        <div className="confidence-bar" style={{ width: `${(field.confidence || 0) * 100}%` }} />
                                      </div>
                                      <span className="confidence-label">{((field.confidence || 0) * 100).toFixed(0)}%</span>
                                    </div>
                                  </td>
                                  <td className="source-span">{field.source_span || '—'}</td>
                                </tr>
                              ))
                              : (
                                <tr>
                                  <td colSpan={4} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 'var(--spacing-xl)', fontStyle: 'italic' }}>
                                    No fields extracted
                                  </td>
                                </tr>
                              )
                            }
                          </tbody>
                        </table>
                      </div>
                      <div className="action-bar">
                        <button
                          className="btn-secondary"
                          onClick={() => {
                            if (selectedDocument?.dataOnly) {
                              navigator.clipboard.writeText(JSON.stringify(selectedDocument.dataOnly, null, 2));
                            }
                          }}
                        >
                          Copy Data
                        </button>
                        <button
                          className="btn-primary"
                          onClick={() => {
                            if (selectedDocument?.dataOnly) {
                              const blob = new Blob([JSON.stringify(selectedDocument.dataOnly, null, 2)], { type: 'application/json' });
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = url;
                              a.download = `${selectedDocument.file.name.replace(/\.[^/.]+$/, '')}.json`;
                              a.click();
                              URL.revokeObjectURL(url);
                            }
                          }}
                        >
                          Save JSON
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="empty-state">Processing document...</div>
                  )}
                </>
              )}
            </>
          )}

          {currentTab === 'evaluation' && (
            <div className="evaluation-tab-content">
              <h3 className="section-title">Evaluation Metrics</h3>
              <p className="eval-description">Aggregated from Judge agent scores across all completed documents in this session.</p>
              {!evaluationMetrics ? (
                <div className="empty-state">
                  No evaluation run yet.<br />
                  <span style={{ fontSize: '11px', marginTop: '8px', display: 'block' }}>Upload documents and wait for the Judge agent to score them.</span>
                </div>
              ) : (
                <div className="evaluation-metrics">
                  <div className="metric-card">
                    <div className="value" style={{ color: 'var(--color-status-validated)' }}>{evaluationMetrics.f1}</div>
                    <div className="label">Avg Judge Score</div>
                  </div>
                  <div className="metric-card">
                    <div className="value" style={{ color: 'var(--color-text-muted)' }}>{evaluationMetrics.judgedCount}</div>
                    <div className="label">Docs Judged</div>
                  </div>
                  <div className="metric-card">
                    <div className="value" style={{ color: 'var(--color-status-flagged)' }}>
                      {documentQueue.filter(d => d.status === 'flagged').length}
                    </div>
                    <div className="label">Flagged</div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default App;
