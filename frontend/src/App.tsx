import { useEffect, useState, useRef, type ChangeEvent, type DragEvent } from 'react';

/* -------------------------------------------------------------------------- */
/*                                 Type Defs                                  */
/* -------------------------------------------------------------------------- */

type GroupStatus = 'queued' | 'uploading' | 'done' | 'error';

type ExtractionField = { value: unknown; confidence: number; source_span?: string | null; likely_required?: boolean };

type ValidationIssue = { field: string; message: string; severity: string };

type ValidationResult = { is_valid: boolean; completeness_score: number; issues: ValidationIssue[] };

type JudgeResult = { score: number; issues: Array<{ field: string; message: string; severity: string }>; notes: string };

type ExtractionResult = {
  id: string;
  doc_type: string | null;
  language: string | null;
  routing_reason?: string | null;
  suggested_fields: Array<{ name: string; description?: string | null; likely_required: boolean }>;
  extracted_fields: Record<string, ExtractionField>;
  additional_fields: Record<string, ExtractionField>;
  validation: ValidationResult | null;
  judge: JudgeResult | null;
  needs_review: boolean;
  error?: string | null;
};

type FileExtractionResponse = {
  request: { filename: string; content_type?: string | null; size_bytes?: number | null };
  documents: ExtractionResult[];
  error?: string | null;
};

type EvaluateMismatch = { field: string; predicted?: unknown; expected?: unknown };

type EvaluateResponse = {
  score: number;
  precision: number;
  recall: number;
  f1: number;
  summary: string;
  mismatches: EvaluateMismatch[];
};

type DocumentGroup = {
  id: string;
  label: string;
  files: File[];
  status: GroupStatus;
  response?: FileExtractionResponse;
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
/*                                  Icons                                     */
/* -------------------------------------------------------------------------- */

const UploadIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);

const AlertTriangleIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
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
  const [groups, setGroups] = useState<DocumentGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedDocIndex, setSelectedDocIndex] = useState<number>(0);
  const [currentTab, setCurrentTab] = useState<'extraction' | 'evaluation'>('extraction');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [groundTruthDrafts, setGroundTruthDrafts] = useState<Record<string, string>>({});
  const [evaluations, setEvaluations] = useState<Record<string, EvaluateResponse>>({});
  const [evalErrors, setEvalErrors] = useState<Record<string, string>>({});
  const [evaluating, setEvaluating] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetch(`${apiBaseUrl}/`)
      .then((r) => r.json())
      .then((d: ApiRoot) => {
        if (d?.recommended_extraction_model?.display_name) setModelName(d.recommended_extraction_model.display_name);
      })
      .catch(() => undefined);
  }, []);

  const selectedGroup = groups.find((g) => g.id === selectedGroupId) || null;
  const selectedDoc: ExtractionResult | null = selectedGroup?.response?.documents?.[selectedDocIndex] ?? null;

  /* ---- Upload handling: files selected/dropped TOGETHER = one group (pages of one doc) ---- */

  const addGroupFromFiles = (files: File[]) => {
    if (files.length === 0) return;
    const label = files.length === 1 ? files[0].name : `${files.length} files (${files[0].name}, ...)`;
    const group: DocumentGroup = {
      id: crypto.randomUUID(),
      label,
      files,
      status: 'queued',
    };
    setGroups((prev) => [...prev, group]);
    setSelectedGroupId(group.id);
    setSelectedDocIndex(0);
    processGroup(group);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    addGroupFromFiles(files);
    event.target.value = '';
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files || []);
    addGroupFromFiles(files);
  };

  const processGroup = async (group: DocumentGroup) => {
    setGroups((prev) => prev.map((g) => (g.id === group.id ? { ...g, status: 'uploading' } : g)));

    try {
      const form = new FormData();
      for (const file of group.files) {
        form.append('files', file);
      }

      const res = await fetch(`${apiBaseUrl}/extract`, { method: 'POST', body: form });
      const payload = (await res.json()) as FileExtractionResponse & { detail?: string };

      if (!res.ok) {
        throw new Error((payload as { detail?: string }).detail ?? `Extract failed for ${group.label}`);
      }

      setGroups((prev) =>
        prev.map((g) => (g.id === group.id ? { ...g, status: 'done', response: payload } : g)),
      );
    } catch (err) {
      setGroups((prev) =>
        prev.map((g) =>
          g.id === group.id ? { ...g, status: 'error', error: err instanceof Error ? err.message : String(err) } : g,
        ),
      );
    }
  };

  /* ---- Derived display data for the selected document ---- */

  const combinedFields: Array<[string, ExtractionField]> = selectedDoc
    ? [
        ...Object.entries(selectedDoc.extracted_fields || {}),
        ...Object.entries(selectedDoc.additional_fields || {}),
      ]
    : [];

  const validationErrors = selectedDoc?.validation?.issues?.filter((i) => i.severity === 'error') || [];
  const completeness = selectedDoc?.validation?.completeness_score;

  const currentPipelineStage = (doc: ExtractionResult | null): number => {
    if (!doc) return 0;
    if (doc.error) return 2; // stopped at validator stage with an error
    return 4; // fully processed (router -> extractor -> validator -> judge all ran)
  };

  /* ---- Evaluation tab: compare extracted fields against a ground truth JSON ---- */

  const evalKey = selectedGroup && selectedDoc ? `${selectedGroup.id}:${selectedDocIndex}` : null;
  const groundTruthDraft = evalKey ? groundTruthDrafts[evalKey] ?? '' : '';
  const evaluation = evalKey ? evaluations[evalKey] : undefined;
  const evalError = evalKey ? evalErrors[evalKey] : undefined;
  const isEvaluating = evalKey ? !!evaluating[evalKey] : false;

  const setGroundTruthDraft = (value: string) => {
    if (!evalKey) return;
    setGroundTruthDrafts((prev) => ({ ...prev, [evalKey]: value }));
  };

  const handleGroundTruthFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !evalKey) return;
    const reader = new FileReader();
    reader.onload = () => setGroundTruthDraft(String(reader.result ?? ''));
    reader.readAsText(file);
    event.target.value = '';
  };

  const prefillGroundTruthFromExtracted = () => {
    if (!evalKey) return;
    const merged = Object.fromEntries(combinedFields.map(([name, field]) => [name, field.value]));
    setGroundTruthDraft(JSON.stringify(merged, null, 2));
  };

  const runEvaluation = async () => {
    if (!evalKey || !selectedDoc) return;

    let groundTruth: Record<string, unknown>;
    try {
      const parsed = JSON.parse(groundTruthDraft);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('not an object');
      }
      groundTruth = parsed as Record<string, unknown>;
    } catch {
      setEvalErrors((prev) => ({ ...prev, [evalKey]: 'Ground truth must be valid JSON, e.g. {"invoice_number": "INV-001"}.' }));
      return;
    }

    setEvalErrors((prev) => {
      const next = { ...prev };
      delete next[evalKey];
      return next;
    });
    setEvaluating((prev) => ({ ...prev, [evalKey]: true }));

    const prediction = Object.fromEntries(combinedFields.map(([name, field]) => [name, field.value]));

    try {
      const res = await fetch(`${apiBaseUrl}/evaluate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_type: selectedDoc.doc_type || 'unknown',
          prediction,
          ground_truth: groundTruth,
        }),
      });
      const payload = (await res.json()) as EvaluateResponse & { detail?: string };
      if (!res.ok) {
        throw new Error((payload as { detail?: string }).detail ?? 'Evaluation failed');
      }
      setEvaluations((prev) => ({ ...prev, [evalKey]: payload }));
    } catch (err) {
      setEvalErrors((prev) => ({ ...prev, [evalKey]: err instanceof Error ? err.message : String(err) }));
    } finally {
      setEvaluating((prev) => ({ ...prev, [evalKey]: false }));
    }
  };

  /* -------------------------------------------------------------------------- */

  return (
    <>
      <div className="sidebar">
        <h2 style={{ fontSize: 'var(--font-size-md)', margin: '0 0 var(--spacing-md) 0' }}>
          Document Queue <span style={{ color: 'var(--color-text-muted)' }}>({groups.length})</span>
        </h2>

        <div
          className="upload-dropzone"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
        >
          <UploadIcon />
          <span>Drag &amp; Drop or Click to Upload Files</span>
          <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--color-text-muted)' }}>
            Select multiple files together to treat them as pages of one document
          </span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,application/pdf"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        <ul className="document-list">
          {groups.length === 0 ? (
            <li style={{ color: 'var(--color-text-muted)', textAlign: 'center', padding: 'var(--spacing-md)' }}>
              No documents uploaded yet.
            </li>
          ) : (
            groups.map((group) => (
              <li
                key={group.id}
                className={`document-list-item ${group.id === selectedGroupId ? 'active' : ''}`}
                onClick={() => {
                  setSelectedGroupId(group.id);
                  setSelectedDocIndex(0);
                }}
              >
                <span className="file-name">{group.label}</span>
                <span className={`status-indicator ${group.status}`}></span>
              </li>
            ))
          )}
        </ul>
      </div>

      <div className="main-content">
        <div className="panel" style={{ padding: 'var(--spacing-lg)' }}>
          <h1 style={{ fontFamily: 'var(--font-family-sans)', fontSize: 'var(--font-size-xl)', margin: '0 0 var(--spacing-md) 0' }}>
            Extraction Console{' '}
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)' }}>({modelName})</span>
          </h1>

          <div
            className="tabs"
            style={{ display: 'flex', gap: 'var(--spacing-md)', marginBottom: 'var(--spacing-lg)', borderBottom: '1px solid var(--color-border)' }}
          >
            <button
              style={{
                padding: 'var(--spacing-sm) var(--spacing-md)',
                border: 'none',
                background: 'none',
                cursor: 'pointer',
                color: currentTab === 'extraction' ? 'var(--color-text-default)' : 'var(--color-text-muted)',
                borderBottom: currentTab === 'extraction' ? '2px solid var(--color-status-agent)' : 'none',
              }}
              onClick={() => setCurrentTab('extraction')}
            >
              Extraction
            </button>
            <button
              style={{
                padding: 'var(--spacing-sm) var(--spacing-md)',
                border: 'none',
                background: 'none',
                cursor: 'pointer',
                color: currentTab === 'evaluation' ? 'var(--color-text-default)' : 'var(--color-text-muted)',
                borderBottom: currentTab === 'evaluation' ? '2px solid var(--color-status-agent)' : 'none',
              }}
              onClick={() => setCurrentTab('evaluation')}
            >
              Evaluation
            </button>
          </div>

          {currentTab === 'extraction' && (
            <>
              {!selectedGroup ? (
                <div className="empty-state">Select a document from the queue to view its extraction details.</div>
              ) : selectedGroup.status === 'error' ? (
                <div className="warning-box">
                  <AlertTriangleIcon />
                  <div>
                    <h3 style={{ margin: '0 0 var(--spacing-xs) 0' }}>Upload Failed</h3>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>{selectedGroup.error}</p>
                  </div>
                </div>
              ) : selectedGroup.status !== 'done' ? (
                <div className="empty-state">Processing document...</div>
              ) : (selectedGroup.response?.documents.length ?? 0) === 0 ? (
                <div className="warning-box">
                  <AlertTriangleIcon />
                  <div>
                    <h3 style={{ margin: '0 0 var(--spacing-xs) 0' }}>No Documents Detected</h3>
                    <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>
                      {selectedGroup.response?.error || 'No readable pages were found in the uploaded file(s).'}
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  {selectedGroup.response!.documents.length > 1 && (
                    <div className="page-tabs" style={{ display: 'flex', gap: 'var(--spacing-xs)', marginBottom: 'var(--spacing-md)', flexWrap: 'wrap' }}>
                      {selectedGroup.response!.documents.map((doc, idx) => (
                        <button
                          key={doc.id}
                          onClick={() => setSelectedDocIndex(idx)}
                          style={{
                            padding: 'var(--spacing-xs) var(--spacing-sm)',
                            borderRadius: 'var(--border-radius-sm)',
                            border: '1px solid var(--color-border)',
                            background: idx === selectedDocIndex ? 'var(--color-status-agent)' : 'var(--color-panel-background)',
                            color: idx === selectedDocIndex ? 'var(--color-background)' : 'var(--color-text-default)',
                            cursor: 'pointer',
                            fontSize: 'var(--font-size-xs)',
                          }}
                        >
                          Page {idx + 1}: {doc.doc_type || 'unknown'}
                        </button>
                      ))}
                    </div>
                  )}

                  <div className="stepper">
                    {PIPELINE_STEPS.map((step, index) => (
                      <div
                        key={step}
                        className={`step ${index < currentPipelineStage(selectedDoc) ? 'completed' : ''} ${
                          index === currentPipelineStage(selectedDoc) ? 'active' : ''
                        }`}
                      >
                        <div className="step-circle">{index + 1}</div>
                        <div
                          className="step-label"
                          style={{ color: index === currentPipelineStage(selectedDoc) ? 'var(--color-status-agent)' : '' }}
                        >
                          {step}
                        </div>
                      </div>
                    ))}
                  </div>

                  {selectedDoc?.error && (
                    <div className="warning-box">
                      <AlertTriangleIcon />
                      <div>
                        <h3 style={{ margin: '0 0 var(--spacing-xs) 0' }}>Processing Error</h3>
                        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>{selectedDoc.error}</p>
                      </div>
                    </div>
                  )}

                  {!selectedDoc?.error && validationErrors.length > 0 && (
                    <div className="warning-box">
                      <AlertTriangleIcon />
                      <div>
                        <h3 style={{ margin: '0 0 var(--spacing-xs) 0', fontSize: 'var(--font-size-lg)' }}>Document Flagged for Review</h3>
                        <p style={{ margin: '0 0 var(--spacing-sm) 0', fontSize: 'var(--font-size-sm)' }}>The following validation errors were found:</p>
                        <ul style={{ listStyleType: 'disc', marginLeft: 'var(--spacing-md)' }}>
                          {validationErrors.map((error, index) => (
                            <li key={index}>
                              <strong>{error.field}:</strong> {error.message}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  )}
                  {!selectedDoc?.error && selectedDoc?.judge && (
                    <div
                      className={selectedDoc.judge.score < 0.7 ? 'warning-box' : 'info-box'}
                      style={{ marginTop: 'var(--spacing-md)' }}
                    >
                      {selectedDoc.judge.score < 0.7 ? <AlertTriangleIcon /> : null}
                      <div>
                        <h3 style={{ margin: '0 0 var(--spacing-xs) 0', fontSize: 'var(--font-size-lg)' }}>
                          Judge Review — {Math.round(selectedDoc.judge.score * 100)}% confidence
                        </h3>
                        {selectedDoc.judge.notes && (
                          <p style={{ margin: '0 0 var(--spacing-sm) 0', fontSize: 'var(--font-size-sm)' }}>
                            {selectedDoc.judge.notes}
                          </p>
                        )}
                        {selectedDoc.judge.issues.length > 0 && (
                          <>
                            <p style={{ margin: '0 0 var(--spacing-sm) 0', fontSize: 'var(--font-size-sm)' }}>
                              Flagged fields:
                            </p>
                            <ul style={{ listStyleType: 'disc', marginLeft: 'var(--spacing-md)' }}>
                              {selectedDoc.judge.issues.map((issue, index) => (
                                <li key={index}>
                                  <strong>{issue.field}</strong> [{issue.severity}]: {issue.message}
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                  {!selectedDoc?.error && selectedDoc?.judge && (
                    <div
                      className={selectedDoc.judge.score < 0.7 ? 'warning-box' : 'info-box'}
                      style={{ marginTop: 'var(--spacing-md)' }}
                    >
                      {selectedDoc.judge.score < 0.7 ? <AlertTriangleIcon /> : null}
                      <div>
                        <h3 style={{ margin: '0 0 var(--spacing-xs) 0', fontSize: 'var(--font-size-lg)' }}>
                          Judge Review — {Math.round(selectedDoc.judge.score * 100)}% confidence
                        </h3>
                        {selectedDoc.judge.notes && (
                          <p style={{ margin: '0 0 var(--spacing-sm) 0', fontSize: 'var(--font-size-sm)' }}>
                            {selectedDoc.judge.notes}
                          </p>
                        )}
                        {selectedDoc.judge.issues.length > 0 && (
                          <>
                            <p style={{ margin: '0 0 var(--spacing-sm) 0', fontSize: 'var(--font-size-sm)' }}>
                              Flagged fields:
                            </p>
                            <ul style={{ listStyleType: 'disc', marginLeft: 'var(--spacing-md)' }}>
                              {selectedDoc.judge.issues.map((issue, index) => (
                                <li key={index}>
                                  <strong>{issue.field}</strong> [{issue.severity}]: {issue.message}
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  {!selectedDoc?.error && (
                    <>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-sm)', margin: 'var(--spacing-md) 0' }}>
                        <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)' }}>Field completeness:</span>
                        <div className="confidence-bar-container" style={{ flex: 1, maxWidth: '300px' }}>
                          <div className="confidence-bar" style={{ width: `${(completeness ?? 0) * 100}%` }}></div>
                        </div>
                        <span style={{ fontSize: 'var(--font-size-sm)' }}>{Math.round((completeness ?? 0) * 100)}% complete</span>
                      </div>

                      <h3 style={{ fontFamily: 'var(--font-family-sans)', fontSize: 'var(--font-size-lg)', margin: 'var(--spacing-lg) 0 var(--spacing-md) 0' }}>
                        Extracted Fields
                      </h3>
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
                          {combinedFields.map(([key, field]) => (
                            <tr key={key}>
                              <td style={{ fontFamily: 'var(--font-family-mono)' }}>{key}</td>
                              <td>
                                {typeof field.value === 'object' ? JSON.stringify(field.value) : String(field.value ?? '')}
                              </td>
                              <td>
                                <div className="confidence-bar-container">
                                  <div className="confidence-bar" style={{ width: `${(field.confidence || 0) * 100}%` }}></div>
                                </div>
                              </td>
                              <td style={{ fontFamily: 'var(--font-family-mono)', fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)' }}>
                                {field.source_span || 'N/A'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>

                      <div style={{ marginTop: 'var(--spacing-lg)', textAlign: 'right' }}>
                        <button
                          style={{
                            padding: 'var(--spacing-sm) var(--spacing-md)',
                            borderRadius: 'var(--border-radius-md)',
                            border: '1px solid var(--color-border)',
                            background: 'var(--color-panel-background)',
                            color: 'var(--color-text-default)',
                            cursor: 'pointer',
                            marginRight: 'var(--spacing-sm)',
                          }}
                          onClick={() => {
                            const merged = Object.fromEntries(combinedFields);
                            navigator.clipboard.writeText(JSON.stringify(merged, null, 2));
                          }}
                        >
                          Copy Data
                        </button>
                        <button
                          style={{
                            padding: 'var(--spacing-sm) var(--spacing-md)',
                            borderRadius: 'var(--border-radius-md)',
                            border: 'none',
                            background: 'var(--color-status-agent)',
                            color: 'var(--color-background)',
                            cursor: 'pointer',
                          }}
                          onClick={() => {
                            const merged = Object.fromEntries(combinedFields);
                            const blob = new Blob([JSON.stringify(merged, null, 2)], { type: 'application/json' });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = `${(selectedGroup.label || 'document').replace(/\.[^/.]+$/, '')}.json`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          Save JSON
                        </button>
                      </div>
                    </>
                  )}
                </>
              )}
            </>
          )}

          {currentTab === 'evaluation' && (
            <div className="evaluation-tab-content">
              <h3 style={{ fontFamily: 'var(--font-family-sans)', fontSize: 'var(--font-size-lg)', margin: '0 0 var(--spacing-md) 0' }}>
                Evaluation Metrics
              </h3>

              {!selectedDoc ? (
                <div className="empty-state">Select a document from the queue to evaluate its extraction.</div>
              ) : (
                <>
                  <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)', margin: '0 0 var(--spacing-sm) 0' }}>
                    Paste or upload the expected (ground truth) field values as JSON to score this document's
                    extracted fields against them.
                  </p>

                  <div style={{ display: 'flex', gap: 'var(--spacing-sm)', marginBottom: 'var(--spacing-sm)' }}>
                    <label
                      style={{
                        padding: 'var(--spacing-xs) var(--spacing-sm)',
                        border: '1px solid var(--color-border)',
                        borderRadius: 'var(--border-radius-sm)',
                        cursor: 'pointer',
                        fontSize: 'var(--font-size-xs)',
                        color: 'var(--color-text-muted)',
                        display: 'inline-flex',
                        alignItems: 'center',
                      }}
                    >
                      Upload Ground Truth JSON
                      <input type="file" accept="application/json,.json" style={{ display: 'none' }} onChange={handleGroundTruthFile} />
                    </label>
                    <button
                      onClick={prefillGroundTruthFromExtracted}
                      style={{
                        padding: 'var(--spacing-xs) var(--spacing-sm)',
                        borderRadius: 'var(--border-radius-sm)',
                        border: '1px solid var(--color-border)',
                        background: 'var(--color-panel-background)',
                        color: 'var(--color-text-muted)',
                        cursor: 'pointer',
                        fontSize: 'var(--font-size-xs)',
                      }}
                    >
                      Prefill From Extracted Fields
                    </button>
                  </div>

                  <textarea
                    value={groundTruthDraft}
                    onChange={(e) => setGroundTruthDraft(e.target.value)}
                    placeholder={'{\n  "invoice_number": "INV-001",\n  "total": "1234.56"\n}'}
                    rows={10}
                    style={{
                      width: '100%',
                      fontFamily: 'var(--font-family-mono)',
                      fontSize: 'var(--font-size-sm)',
                      background: 'var(--color-panel-background)',
                      color: 'var(--color-text-default)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 'var(--border-radius-md)',
                      padding: 'var(--spacing-sm)',
                      resize: 'vertical',
                    }}
                  />

                  {evalError && (
                    <div className="warning-box" style={{ marginTop: 'var(--spacing-sm)' }}>
                      <AlertTriangleIcon />
                      <div>
                        <p style={{ margin: 0, fontSize: 'var(--font-size-sm)' }}>{evalError}</p>
                      </div>
                    </div>
                  )}

                  <div style={{ margin: 'var(--spacing-md) 0 var(--spacing-lg) 0' }}>
                    <button
                      onClick={runEvaluation}
                      disabled={isEvaluating || !groundTruthDraft.trim()}
                      style={{
                        padding: 'var(--spacing-sm) var(--spacing-md)',
                        borderRadius: 'var(--border-radius-md)',
                        border: 'none',
                        background: 'var(--color-status-agent)',
                        color: 'var(--color-background)',
                        cursor: isEvaluating || !groundTruthDraft.trim() ? 'default' : 'pointer',
                        opacity: isEvaluating || !groundTruthDraft.trim() ? 0.6 : 1,
                      }}
                    >
                      {isEvaluating ? 'Evaluating…' : 'Run Evaluation'}
                    </button>
                  </div>

                  {!evaluation ? (
                    <div className="empty-state">No evaluation run yet.</div>
                  ) : (
                    <>
                      <div className="evaluation-metrics">
                        <div className="metric-card">
                          <div className="value">{Math.round(evaluation.score * 100)}%</div>
                          <div className="label">Score</div>
                        </div>
                        <div className="metric-card">
                          <div className="value">{Math.round(evaluation.precision * 100)}%</div>
                          <div className="label">Precision</div>
                        </div>
                        <div className="metric-card">
                          <div className="value">{Math.round(evaluation.recall * 100)}%</div>
                          <div className="label">Recall</div>
                        </div>
                        <div className="metric-card">
                          <div className="value">{Math.round(evaluation.f1 * 100)}%</div>
                          <div className="label">F1</div>
                        </div>
                      </div>

                      <p style={{ fontSize: 'var(--font-size-sm)', color: 'var(--color-text-muted)', margin: 'var(--spacing-md) 0' }}>
                        {evaluation.summary}
                      </p>

                      {evaluation.mismatches.length > 0 && (
                        <>
                          <h3
                            style={{
                              fontFamily: 'var(--font-family-sans)',
                              fontSize: 'var(--font-size-lg)',
                              margin: 'var(--spacing-lg) 0 var(--spacing-md) 0',
                            }}
                          >
                            Mismatches ({evaluation.mismatches.length})
                          </h3>
                          <table className="extracted-fields-table">
                            <thead>
                              <tr>
                                <th>Field Name</th>
                                <th>Predicted</th>
                                <th>Expected</th>
                              </tr>
                            </thead>
                            <tbody>
                              {evaluation.mismatches.map((m, idx) => (
                                <tr key={`${m.field}-${idx}`}>
                                  <td style={{ fontFamily: 'var(--font-family-mono)' }}>{m.field}</td>
                                  <td>{typeof m.predicted === 'object' ? JSON.stringify(m.predicted) : String(m.predicted ?? '—')}</td>
                                  <td>{typeof m.expected === 'object' ? JSON.stringify(m.expected) : String(m.expected ?? '—')}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default App;