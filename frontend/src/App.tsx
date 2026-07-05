import { useEffect, useState, type FormEvent } from 'react';
import { recognize } from 'tesseract.js';

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

type BatchResult = {
  fileName: string;
  dataOnly: Record<string, unknown>;
  extraction: ExtractionResult;
};

type ApiRoot = {
  recommended_extraction_model?: { display_name: string; reason: string };
};

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

function isImageFile(file: File) {
  return file.type.startsWith('image/') || /\.(png|jpg|jpeg|webp|gif|bmp|tif|tiff)$/i.test(file.name);
}

async function ocrImage(file: File): Promise<string> {
  const result = await recognize(file, 'eng');
  return result.data.text.trim();
}

function App() {
  const [modelName, setModelName] = useState<string>('Model');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [results, setResults] = useState<BatchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${apiBaseUrl}/`)
      .then((r) => r.json())
      .then((d: ApiRoot) => {
        if (d?.recommended_extraction_model?.display_name) setModelName(d.recommended_extraction_model.display_name);
      })
      .catch(() => undefined);
  }, []);

  const reset = () => {
    setSelectedFiles([]);
    setResults([]);
    setError(null);
  };

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (selectedFiles.length === 0) {
      setError('Select a file');
      return;
    }

    setBusy(true);
    setError(null);
    setResults([]);

    try {
      const outputs: BatchResult[] = [];

      for (const file of selectedFiles) {
        const form = new FormData();
        form.append('file', file);

        if (isImageFile(file)) {
          const ocrText = await ocrImage(file);
          if (!ocrText) throw new Error(`OCR failed: ${file.name}`);
          form.append('ocr_text', ocrText);
        }

        const res = await fetch(`${apiBaseUrl}/extract`, { method: 'POST', body: form });
        const payload = (await res.json()) as ExtractionResult & { detail?: string };
        if (!res.ok) throw new Error(payload.detail ?? `Extract failed: ${file.name}`);

        const dataOnly: Record<string, unknown> = Object.fromEntries(
          Object.entries(payload.extracted_fields).map(([k, v]) => [k, v.value]),
        );

        outputs.push({ fileName: file.name, dataOnly, extraction: payload });
      }

      setResults(outputs);
      setSelectedFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extraction failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="shell" style={{ maxWidth: 980 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 18 }}>
        <div>
          <div style={{ color: '#63e6be', letterSpacing: '0.12em', textTransform: 'uppercase', fontWeight: 700, fontSize: 12 }}>
            DocExtract
          </div>
          <h1 style={{ margin: '6px 0 0', fontSize: 24 }}>Extract clean JSON</h1>
        </div>
        <div style={{ color: '#9eb1cb' }}>{modelName}</div>
      </header>

      <form className="panel" onSubmit={submit} style={{ padding: 18, marginBottom: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
          <input
            type="file"
            multiple
            accept=".txt,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,image/*"
            onChange={(e) => setSelectedFiles(Array.from(e.target.files ?? []))}
          />
          <button type="submit" disabled={busy} style={{ padding: '10px 14px', borderRadius: 12, border: 'none', background: '#63e6be', color: '#06111f', fontWeight: 700 }}>
            {busy ? 'Extracting…' : 'Extract'}
          </button>
          <button type="button" onClick={reset} style={{ padding: '10px 14px', borderRadius: 12, border: '1px solid rgba(148,163,184,0.2)', background: 'transparent', color: '#ecf4ff' }}>
            Clear
          </button>
        </div>
        {error ? <p style={{ color: '#ff7b8c', margin: '10px 0 0' }}>{error}</p> : null}
      </form>

      {results.length === 0 ? (
        <div className="empty-state">Upload a document to see extracted data here.</div>
      ) : (
        <div style={{ display: 'grid', gap: 14 }}>
          {results.map((r) => (
            <section key={r.fileName} className="panel" style={{ padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
                <div style={{ fontWeight: 700 }}>{r.fileName}</div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    className="copy-btn"
                    style={{ width: 'auto' }}
                    type="button"
                    onClick={() => navigator.clipboard.writeText(JSON.stringify(r.dataOnly, null, 2))}
                  >
                    Copy
                  </button>
                  <button
                    className="save-btn"
                    type="button"
                    onClick={() => {
                      const blob = new Blob([JSON.stringify(r.dataOnly, null, 2)], { type: 'application/json' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `${r.fileName.replace(/\.[^/.]+$/, '')}.json`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    Save JSON
                  </button>
                </div>
              </div>
              <pre style={{ margin: '10px 0 0', padding: 12, background: 'rgba(0,0,0,0.3)', borderRadius: 12, border: '1px solid rgba(148,163,184,0.1)', overflowX: 'auto', fontSize: 12, color: '#ecf4ff' }}>
                {JSON.stringify(r.dataOnly, null, 2)}
              </pre>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}

export default App;
