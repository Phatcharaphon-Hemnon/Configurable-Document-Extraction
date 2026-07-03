import { useEffect, useMemo, useState } from 'react';

type ApiStatus = {
  name: string;
  status: string;
  frontend_origins: string[];
  supported_doc_types: string[];
  endpoints: string[];
};

type ExtractionResult = Record<string, unknown>;

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadedTemplates, setLoadedTemplates] = useState<number | null>(null);

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

  const submitDocument = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!selectedFile) {
      setError('Choose a document file first.');
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const response = await fetch(`${apiBaseUrl}/extract`, {
        method: 'POST',
        body: formData,
      });

      const payload = (await response.json()) as ExtractionResult & { detail?: string };

      if (!response.ok) {
        throw new Error(payload.detail || 'Extraction failed');
      }

      setResult(payload);
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
          <h1>Upload a document and call the backend extraction API.</h1>
          <p className="lede">
            This frontend is a separate React app. It sends files to the FastAPI service, shows the JSON result,
            and keeps the backend logic isolated from the UI.
          </p>

          <div className="status-row">
            <span className={apiStatus ? 'pill success' : 'pill muted'}>
              {apiStatus ? `Backend: ${apiStatus.status}` : 'Backend status unavailable'}
            </span>
            <span className="pill muted">
              {loadedTemplates === null ? 'Templates unavailable' : `${loadedTemplates} template groups loaded`}
            </span>
          </div>
        </div>

        <aside className="panel side-panel">
          <h2>Connected API</h2>
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
              <dt>Database</dt>
              <dd>No persistent DB yet</dd>
            </div>
          </dl>
        </aside>
      </section>

      <section className="content-grid">
        <form className="panel form-panel" onSubmit={submitDocument}>
          <div className="section-head">
            <h2>Extract document</h2>
            <p>Use the backend route <span>/extract</span>.</p>
          </div>

          <label className="upload-box">
            <input
              type="file"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
            <span>{selectedFile ? selectedFile.name : 'Choose invoice, PO, or delivery note file'}</span>
          </label>

          <div className="actions">
            <button type="submit" disabled={busy}>
              {busy ? 'Extracting...' : 'Send to backend'}
            </button>
            {error ? <p className="message error">{error}</p> : <p className="message">Ready to submit.</p>}
          </div>
        </form>

        <section className="panel result-panel">
          <div className="section-head">
            <h2>Result</h2>
            <p>{apiStatus?.endpoints?.includes('/extract') ? 'Backend response' : 'Waiting for response'}</p>
          </div>

          <pre>{result ? JSON.stringify(result, null, 2) : 'Upload a file to see JSON output here.'}</pre>
        </section>
      </section>
    </main>
  );
}

export default App;