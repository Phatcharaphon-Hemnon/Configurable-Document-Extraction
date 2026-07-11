import { useEffect, useState, useRef, type FormEvent, type ChangeEvent } from \'react\';
import { recognize } from \'tesseract.js\';

/* -------------------------------------------------------------------------- */
/*                                 Type Defs                                  */
/* -------------------------------------------------------------------------- */

type DocumentStatus = 'queued' | 'classifying' | 'extracting' | 'validating' | 'done' | 'flagged';

type ExtractionField = { value: unknown; confidence: number; source_span?: string | null };

type ExtractionResult = {
  id: string;
  doc_type: string;
  language: string;
  routing_reason?: string | null;
  extracted_fields: Record<string, ExtractionField>;
  validation: { is_valid: boolean; issues: Array<{ field: string; message: string }> };
  judge?: { score: number; issues: Array<{ field: string; message: string }>; notes: string } | null;
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

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || \'http://localhost:8000/api\';

const PIPELINE_STEPS = [\'Router\', \'Extractor\', \'Validator\', \'Judge\'];

/* -------------------------------------------------------------------------- */
/*                                  Utilities                                 */
/* -------------------------------------------------------------------------- */

function isImageFile(file: File) {
  return file.type.startsWith(\'image/\') || /\\.(png|jpg|jpeg|webp|gif|bmp|tif|tiff)$/i.test(file.name);
}

async function ocrImage(file: File): Promise<string> {
  // Mock OCR for now, replace with actual tesseract.js or backend OCR if needed
  // For demonstration purposes, we'll just return an empty string or a placeholder
  console.log(`Performing OCR on ${file.name}...`);
  const result = await recognize(file, \'eng\');
  return result.data.text.trim();
}

/* -------------------------------------------------------------------------- */
/*                                  Components                                */
/* -------------------------------------------------------------------------- */

const UploadIcon = () => (
  <svg
    xmlns=\"http://www.w3.org/2000/svg\"
    width=\"24\"
    height=\"24\"
    viewBox=\"0 0 24 24\"
    fill=\"none\"
    stroke=\"currentColor\"
    strokeWidth=\"2\"
    strokeLinecap=\"round\"
    strokeLinejoin=\"round\"
  >
    <path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\" />
    <polyline points=\"17 8 12 3 7 8\" />
    <line x1=\"12\" y1=\"3\" x2=\"12\" y2=\"15\" />
  </svg>
);

const AlertTriangleIcon = () => (
  <svg
    xmlns=\"http://www.w3.org/2000/svg\"
    width=\"24\"
    height=\"24\"
    viewBox=\"0 0 24 24\"
    fill=\"none\"
    stroke=\"currentColor\"
    strokeWidth=\"2\"
    strokeLinecap=\"round\"
    strokeLinejoin=\"round\"
  >
    <path d=\"M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z\" />
    <line x1=\"12\" y1=\"9\" x2=\"12\" y2=\"13\" />
    <line x1=\"12\" y1=\"17\" x2=\"12.01\" y2=\"17\" />
  </svg>
);

/* -------------------------------------------------------------------------- */
/*                                   App Main                                 */
/* -------------------------------------------------------------------------- */

function App() {
  const [modelName, setModelName] = useState<string>(\'Model\');
  const [documentQueue, setDocumentQueue] = useState<DocumentInQueue[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [currentTab, setCurrentTab] = useState<\'extraction\' | \'evaluation\'>(\'extraction\');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${apiBaseUrl}/`)
      .then((r) => r.json())
      .then((d: ApiRoot) => {
        if (d?.recommended_extraction_model?.display_name) setModelName(d.recommended_extraction_model.display_name);
      })
      .catch(() => undefined);
  }, []);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (files.length > 0) {
      const newDocuments: DocumentInQueue[] = files.map((file) => ({
        id: crypto.randomUUID(),
        file,
        status: \'queued\',
      }));
      setDocumentQueue((prev) => [...prev, ...newDocuments]);
      if (selectedDocumentId === null) {
        setSelectedDocumentId(newDocuments[0].id);
      }
      processDocuments(newDocuments);
    }
  };

  const processDocuments = async (documentsToProcess: DocumentInQueue[]) => {
    for (const doc of documentsToProcess) {
      // Update status to classifying
      setDocumentQueue((prev) =>
        prev.map((d) => (d.id === doc.id ? { ...d, status: \'classifying\' } : d)),
      );

      try {
        const form = new FormData();
        form.append(\'file\', doc.file);

        if (isImageFile(doc.file)) {
          // Simulate OCR process
          const ocrText = await ocrImage(doc.file);
          if (!ocrText) throw new Error(`OCR failed: ${doc.file.name}`);
          form.append(\'ocr_text\', ocrText);
        }

        // Simulate extraction process
        setDocumentQueue((prev) =>
          prev.map((d) => (d.id === doc.id ? { ...d, status: \'extracting\' } : d)),
        );
        const res = await fetch(`${apiBaseUrl}/extract`, { method: \'POST\', body: form });
        const payload = (await res.json()) as ExtractionResult & { detail?: string };

        if (!res.ok) {
          throw new Error(payload.detail ?? `Extract failed for ${doc.file.name}`);
        }

        // Simulate validation process
        setDocumentQueue((prev) =>
          prev.map((d) => (d.id === doc.id ? { ...d, status: \'validating\' } : d)),
        );

        const dataOnly: Record<string, unknown> = Object.fromEntries(
          Object.entries(payload.extracted_fields).map(([k, v]) => [k, v.value]),
        );

        // Determine final status
        const finalStatus: DocumentStatus = payload.validation.is_valid ? \'done\' : \'flagged\';

        setDocumentQueue((prev) =>
          prev.map((d) =>
            d.id === doc.id
              ? { ...d, status: finalStatus, extractionResult: payload, dataOnly }
              : d,
          ),
        );
      } catch (err) {
        console.error(\`Error processing \${doc.file.name}:\`, err);
        setDocumentQueue((prev) =>
          prev.map((d) =>
            d.id === doc.id
              ? { ...d, status: \'flagged\', error: err instanceof Error ? err.message : \'Unknown error\' }
              : d,
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
      case \'queued\':
        return 0;
      case \'classifying\':
        return 1;
      case \'extracting\':
        return 2;
      case \'validating\':
        return 3;
      case \'done\':
      case \'flagged\':
        return 4; // Or 3 for validator if judge is separate, depends on exact pipeline def
      default:
        return 0;
    }
  };

  const evaluationMetrics = {
    f1: \'N/A\',
    recall: \'N/A\',
    hallucination: \'N/A\',
  };

  return (
    <>
      <div className=\"sidebar panel\">
        <h2 style={{ fontFamily: \'var(--font-family-sans)\', fontSize: \'var(--font-size-lg)\', marginBottom: \'var(--spacing-md)\', color: \'var(--color-text-default)\' }}>
          Document Queue <span style={{ fontSize: \'var(--font-size-sm)\', color: \'var(--color-text-muted)\' }}>({documentQueue.length})</span>
        </h2>
        <div
          className=\"file-upload-container\"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) {
              const newDocuments: DocumentInQueue[] = files.map((file) => ({
                id: crypto.randomUUID(),
                file,
                status: \'queued\',
              }));
              setDocumentQueue((prev) => [...prev, ...newDocuments]);
              if (selectedDocumentId === null) {
                setSelectedDocumentId(newDocuments[0].id);
              }
              processDocuments(newDocuments);
            }
          }}
          style={{ marginBottom: \'var(--spacing-md)\' }}
        >
          <input
            type=\"file\"
            multiple
            accept=\".txt,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,image/*\"
            onChange={handleFileChange}
            ref={fileInputRef}
            className=\"file-upload-input\"
          />
          <UploadIcon />
          <p>Drag & Drop or Click to Upload Files</p>
        </div>

        <ul className=\"document-list\">
          {documentQueue.length === 0 ? (
            <li style={{ color: \'var(--color-text-muted)\', textAlign: \'center\', padding: \'var(--spacing-md)\' }}>No documents uploaded yet.</li>
          ) : (
            documentQueue.map((doc) => (
              <li
                key={doc.id}
                className={`document-list-item ${doc.id === selectedDocumentId ? \'active\' : \'\'}`}
                onClick={() => setSelectedDocumentId(doc.id)}
              >
                <span className=\"file-name\">{doc.file.name}</span>
                <span className={`status-indicator ${doc.status}`}></span>
              </li>
            ))
          )}
        </ul>
      </div>

      <div className=\"main-content\">
        <div className=\"panel\" style={{ padding: \'var(--spacing-lg)\' }}>
          <h1 style={{ fontFamily: \'var(--font-family-sans)\', fontSize: \'var(--font-size-xl)\', margin: \'0 0 var(--spacing-md) 0\' }}>
            Extraction Console <span style={{ fontSize: \'var(--font-size-sm)\', color: \'var(--color-text-muted)\' }}>({modelName})</span>
          </h1>

          <div className=\"tabs\" style={{ display: \'flex\', gap: \'var(--spacing-md)\', marginBottom: \'var(--spacing-lg)\', borderBottom: \'1px solid var(--color-border)\' }}>
            <button
              style={{ padding: \'var(--spacing-sm) var(--spacing-md)\', border: \'none\', background: \'none\', cursor: \'pointer\', color: currentTab === \'extraction\' ? \'var(--color-text-default)\' : \'var(--color-text-muted)\', borderBottom: currentTab === \'extraction\' ? \'2px solid var(--color-status-agent)\' : \'none\' }}
              onClick={() => setCurrentTab(\'extraction\')}
            >
              Extraction
            </button>
            <button
              style={{ padding: \'var(--spacing-sm) var(--spacing-md)\', border: \'none\', background: \'none\', cursor: \'pointer\', color: currentTab === \'evaluation\' ? \'var(--color-text-default)\' : \'var(--color-text-muted)\', borderBottom: currentTab === \'evaluation\' ? \'2px solid var(--color-status-agent)\' : \'none\' }}
              onClick={() => setCurrentTab(\'evaluation\')}
            >
              Evaluation
            </button>
          </div>

          {currentTab === \'extraction\' && (
            <>
              {!selectedDocument ? (
                <div className=\"empty-state\">Select a document from the queue to view its extraction details.</div>
              ) : (
                <>
                  <div className=\"stepper\">
                    {PIPELINE_STEPS.map((step, index) => (
                      <div key={step} className={`step ${index < currentPipelineStage(selectedDocument) ? \'completed\' : \'\'} ${index === currentPipelineStage(selectedDocument) ? \'active\' : \'\'}`}>
                        <div className=\"step-circle\">{index + 1}</div>
                        <div className=\"step-label\" style={{ color: index === currentPipelineStage(selectedDocument) ? \'var(--color-status-agent)\' : \'\' }}>{step}</div>
                      </div>
                    ))}
                  </div>

                  {selectedDocument.status === \'flagged\' && validationErrors && validationErrors.length > 0 && (
                    <div className=\"warning-box\">
                      <AlertTriangleIcon style={{ marginTop: \'4px\' }} />
                      <div>
                        <h3 style={{ margin: \'0 0 var(--spacing-xs) 0\', fontSize: \'var(--font-size-lg)\' }}>Document Flagged for Review</h3>
                        <p style={{ margin: \'0 0 var(--spacing-sm) 0\', fontSize: \'var(--font-size-sm)\' }}>The following validation errors were found:</p>
                        <ul style={{ listStyleType: \'disc\', marginLeft: \'var(--spacing-md)\' }}>
                          {validationErrors.map((error, index) => (
                            <li key={index}>
                              <strong>{error.field}:</strong> {error.message}
                            </li>
                          ))}
                        </ul>
                        {selectedDocument.error && <p style={{ margin: \'var(--spacing-sm) 0 0 0\', fontSize: \'var(--font-size-sm)\' }}>Processing Error: {selectedDocument.error}</p>}
                      </div>
                    </div>
                  )}

                  {selectedDocument.status === \'done\' || selectedDocument.status === \'flagged\' ? (
                    <>
                      <h3 style={{ fontFamily: \'var(--font-family-sans)\', fontSize: \'var(--font-size-lg)\', margin: \'var(--spacing-lg) 0 var(--spacing-md) 0\' }}>Extracted Fields</h3>
                      <table className=\"extracted-fields-table\">
                        <thead>
                          <tr>
                            <th>Field Name</th>
                            <th>Value</th>
                            <th>Confidence</th>
                            <th>Source Span</th>
                          </tr>
                        </thead>
                        <tbody>
                          {extractedFields && Object.entries(extractedFields).map(([key, field]) => (
                            <tr key={key}>
                              <td style={{ fontFamily: \'var(--font-family-mono)\' }}>{key}</td>
                              <td>{JSON.stringify(field.value)}</td>
                              <td>
                                <div className=\"confidence-bar-container\">
                                  <div className=\"confidence-bar\" style={{ width: `${(field.confidence || 0) * 100}%` }}></div>
                                </div>
                              </td>
                              <td style={{ fontFamily: \'var(--font-family-mono)\', fontSize: \'var(--font-size-sm)\', color: \'var(--color-text-muted)\' }}>
                                {field.source_span || \'N/A\'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div style={{ marginTop: \'var(--spacing-lg)\', textAlign: \'right\' }}>
                        <button
                          style={{ padding: \'var(--spacing-sm) var(--spacing-md)\', borderRadius: \'var(--border-radius-md)\', border: \'1px solid var(--color-border)\', background: \'var(--color-panel-background)\', color: \'var(--color-text-default)\', cursor: \'pointer\', marginRight: \'var(--spacing-sm)\' }}
                          onClick={() => {
                            if (selectedDocument?.dataOnly) {
                              navigator.clipboard.writeText(JSON.stringify(selectedDocument.dataOnly, null, 2));
                            }
                          }}
                        >
                          Copy Data
                        </button>
                        <button
                          style={{ padding: \'var(--spacing-sm) var(--spacing-md)\', borderRadius: \'var(--border-radius-md)\', border: \'none\', background: \'var(--color-status-agent)\', color: \'var(--color-background)\', cursor: \'pointer\' }}
                          onClick={() => {
                            if (selectedDocument?.dataOnly) {
                              const blob = new Blob([JSON.stringify(selectedDocument.dataOnly, null, 2)], { type: \'application/json\' });
                              const url = URL.createObjectURL(blob);
                              const a = document.createElement(\'a\');
                              a.href = url;
                              a.download = `${selectedDocument.file.name.replace(/\\.[^/.]+$/, \'\')}.json`;
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
                    <div className=\"empty-state\">Processing document...</div>
                  )}
                </>
              )}
            </>
          )}

          {currentTab === \'evaluation\' && (
            <div className=\"evaluation-tab-content\">
              <h3 style={{ fontFamily: \'var(--font-family-sans)\', fontSize: \'var(--font-size-lg)\', margin: \'0 0 var(--spacing-md) 0\' }}>Evaluation Metrics</h3>
              {evaluationMetrics.f1 === \'N/A\' ? (
                <div className=\"empty-state\">No evaluation run yet.</div>
              ) : (
                <div className=\"evaluation-metrics\">
                  <div className=\"metric-card panel\">
                    <div className=\"value\" style={{ color: \'var(--color-status-validated)\' }}>{evaluationMetrics.f1}</div>
                    <div className=\"label\">F1 Score</div>
                  </div>
                  <div className=\"metric-card panel\">
                    <div className=\"value\" style={{ color: \'var(--color-status-validated)\' }}>{evaluationMetrics.recall}</div>
                    <div className=\"label\">Recall</div>
                  </div>
                  <div className=\"metric-card panel\">
                    <div className=\"value\" style={{ color: \'var(--color-status-flagged)\' }}>{evaluationMetrics.hallucination}</div>
                    <div className=\"label\">Hallucination Rate</div>
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
