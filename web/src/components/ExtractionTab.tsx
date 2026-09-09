import { useEffect, useMemo, useState } from 'react';
import { sourceUrl } from '../api/client';
import { PipelineStepper } from './PipelineStepper';
import { AlertTriangleIcon, CheckCircleIcon } from './icons';
import { JUDGE_PASS_SCORE, formatFieldValue, getPipelineStage } from '../utils/pipeline';
import type { CombinedField, DocumentGroup, ExtractionResult, ProviderErrorDetails } from '../types/extraction';

interface ExtractionTabProps {
  group: DocumentGroup | null;
  doc: ExtractionResult | null;
  docIndex: number;
  onSelectDoc: (index: number) => void;
  onRetry: (id: string) => void;
  combinedFields: CombinedField[];
}

type LineItem = Record<string, unknown>;

function parseLineItems(raw: unknown): LineItem[] | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed.startsWith('[')) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (!Array.isArray(parsed) || parsed.length === 0 || typeof parsed[0] !== 'object') return null;
    return parsed as LineItem[];
  } catch {
    return null;
  }
}

function useImagePreview(group: DocumentGroup | null, doc: ExtractionResult | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!group || group.status !== 'done') {
      setUrl(null);
      return;
    }
    if (doc?.source?.preview_url) {
      setUrl(sourceUrl(doc.source.preview_url) ?? null);
      return;
    }
    const imageFile = group.files.find((f) => f.type.startsWith('image/') && (!doc?.source || f.name === doc.source.filename));
    if (!imageFile) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(imageFile);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [group, doc]);

  return url;
}

function downloadJson(doc: ExtractionResult | null, label: string) {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(label || 'document').replace(/\.[^/.]+$/, '')}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function ProviderErrorBlock({ details }: { details: ProviderErrorDetails }) {
  const rows: Array<[string, string]> = [];
  if (details.provider) rows.push(['Provider', details.provider]);
  if (details.model) rows.push(['Model', details.model]);
  if (details.stage) rows.push(['Stage', details.stage]);
  if (details.status != null) rows.push(['HTTP status', String(details.status)]);
  if (details.code) rows.push(['Code', details.code]);
  if (details.error_type) rows.push(['Error type', details.error_type]);
  if (details.request_id) rows.push(['Request ID', details.request_id]);
  const copyText = JSON.stringify(details, null, 2);
  return (
    <details style={{ marginTop: '8px' }}>
      <summary className="box-text" style={{ cursor: 'pointer', color: '#CBCBCB' }}>
        Provider error details (redacted)
      </summary>
      <div style={{ marginTop: '6px', overflowX: 'auto' }}>
        {rows.map(([k, v]) => (
          <p key={k} className="box-text">
            <strong>{k}:</strong> {v}
          </p>
        ))}
        {details.message && <p className="box-text" style={{ whiteSpace: 'pre-wrap' }}>{details.message}</p>}
        <button
          className="button-secondary"
          style={{ marginTop: '6px' }}
          onClick={() => navigator.clipboard.writeText(copyText)}
        >
          Copy details
        </button>
      </div>
    </details>
  );
}

export function ExtractionTab({ group, doc, docIndex, onSelectDoc, onRetry, combinedFields }: ExtractionTabProps) {
  const [filter, setFilter] = useState('');
  const imagePreviewUrl = useImagePreview(group, doc);

  const filteredFields = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return combinedFields;
    return combinedFields.filter(([name, field]) => {
      const value = formatFieldValue(field.value).toLowerCase();
      return name.toLowerCase().includes(q) || value.includes(q);
    });
  }, [combinedFields, filter]);

  if (!group) {
    return (
      <div className="empty-state">
        <p>Select a document from the queue to view its extraction details.</p>
      </div>
    );
  }

  if (group.status === 'error') {
    return (
      <div className="callout callout-danger">
        <AlertTriangleIcon />
        <div className="callout-body">
          <h3>{group.jobId ? 'Status Check Interrupted' : 'Extraction Failed'}</h3>
          <p className="box-text">{group.error}</p>
          <button className="button-secondary retry-button" onClick={() => onRetry(group.id)}>
            {group.jobId ? 'Reconnect' : 'Retry Upload'}
          </button>
        </div>
      </div>
    );
  }

  if (group.status !== 'done') {
    return (
      <div className="empty-state">
        {group.progress && <p>{group.progress.completed_pages}/{group.progress.total_pages} pages · {group.progress.stage}</p>}
        <p>{group.status === 'queued' ? 'Queued — waiting for an available slot…' : group.status === 'uploading' ? 'Uploading document…' : 'Processing document…'}</p>
      </div>
    );
  }

  const documents = group.response?.documents ?? [];

  if (documents.length === 0) {
    return (
      <div className="callout callout-danger">
        <AlertTriangleIcon />
        <div className="callout-body">
          <h3>No Documents Detected</h3>
          <p className="box-text">{group.response?.error || 'No readable pages were found in the uploaded file(s).'}</p>
          <button className="button-secondary retry-button" onClick={() => onRetry(group.id)}>
            {group.jobId ? 'Reconnect' : 'Retry Upload'}
          </button>
        </div>
      </div>
    );
  }

  const validationErrors = doc?.validation_errors ?? [];
  const completeness = doc?.completeness_score ?? 0;
  const lineItems = doc ? parseLineItems(doc.fields.find((f) => f.name === 'line_items')?.value) : null;

  const fieldsPanel = (
    <>
      {documents.length > 1 && (
        <div className="page-tabs">
          {documents.map((d, idx) => (
            <button key={d.id} className={idx === docIndex ? 'active' : ''} onClick={() => onSelectDoc(idx)}>
              Page {idx + 1}
              <span>{d.source ? `${d.source.filename} · Page ${d.source.page_number}/${d.source.page_count}` : `Page ${idx + 1}`} · {d.language || "?"} · {d.error ? "failed" : d.needs_review ? "review" : "done"}</span>
              <span className="page-tab-type">{d.doc_type.replace(/_/g, ' ')}</span>
            </button>
          ))}
        </div>
      )}

      <div className="doc-meta-row">
        <span className={`doc-type-badge ${doc?.doc_type ?? ''}`}>{doc?.doc_type.replace(/_/g, ' ') ?? '—'}</span>
        {doc?.language && <span className="meta-chip">{doc.language}</span>}
        <div className="doc-meta-stepper">
          <PipelineStepper stage={getPipelineStage(doc)} />
        </div>
      </div>

      {doc?.error && (
        <div className="callout callout-danger">
          <AlertTriangleIcon />
          <div className="callout-body">
            <h3>Processing Error</h3>
            <p className="box-text">{doc.error}</p>
            {doc.error_details && <ProviderErrorBlock details={doc.error_details} />}
          </div>
        </div>
      )}

      {!doc?.error && validationErrors.length > 0 && (
        <div className="callout callout-danger">
          <AlertTriangleIcon />
          <div className="callout-body">
            <h3 className="box-title-lg">Needs Review</h3>
            <ul className="box-list">
              {validationErrors.map((error, index) => (
                <li key={index}>{error}</li>
              ))}
            </ul>
            {doc?.error_details && <ProviderErrorBlock details={doc.error_details} />}
          </div>
        </div>
      )}

      {!doc?.error && doc?.judge && (
        <div className={`callout ${doc.judge.score < JUDGE_PASS_SCORE ? 'callout-danger' : 'callout-ok'} judge-review`}>
          {doc.judge.score < JUDGE_PASS_SCORE ? <AlertTriangleIcon /> : <CheckCircleIcon />}
          <div className="callout-body">
            <div className="judge-head">
              <h3 className="box-title-lg">Judge Review</h3>
              <span className="judge-score">{Math.round(doc.judge.score * 100)}%</span>
            </div>
            {doc.judge.notes && <p className="box-text spaced">{doc.judge.notes}</p>}
            {doc.judge.issues.length > 0 && (
              <ul className="box-list">
                {doc.judge.issues.map((issue, index) => (
                  <li key={index}>
                    <strong>{issue.field}</strong> [{issue.severity}]: {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {!doc?.error && (
        <>
          <div className="completeness-row">
            <span className="completeness-label">Required-field coverage</span>
            <div className="confidence-bar-container completeness-bar">
              <div className="confidence-bar" style={{ width: `${completeness * 100}%` }} />
            </div>
            <span className="completeness-value">{Math.round(completeness * 100)}%</span>
          </div>

          <div className="section-head">
            <h3 className="section-title">Extracted Fields</h3>
            <input
              className="field-filter"
              placeholder="Filter fields…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <span className="field-count">
              {filteredFields.length}/{combinedFields.length}
            </span>
          </div>

          <div className="table-wrap">
            <table className="fields-table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Value</th>
                  <th>Confidence</th>
                  <th>Source Span</th>
                </tr>
              </thead>
              <tbody>
                {filteredFields.map(([key, field]) => {
                  const items = key === 'line_items' ? parseLineItems(field.value) : null;
                  return (
                    <tr key={key}>
                      <td>
                        <span className="field-name">{key}</span>
                        {field.is_new_field && <span className="new-field-tag">new</span>}
                      </td>
                      <td className="field-value">
                        {items ? (
                          <span className="line-items-chip">{items.length} items — see table below</span>
                        ) : (
                          formatFieldValue(field.value)
                        )}
                      </td>
                      <td className="confidence-cell">
                        <div className="confidence-bar-container">
                          <div className="confidence-bar" style={{ width: `${(field.confidence || 0) * 100}%` }} />
                        </div>
                        <span className="confidence-num">{Math.round((field.confidence || 0) * 100)}%</span>
                      </td>
                      <td className="source-span-cell">{field.source_span || '—'}</td>
                    </tr>
                  );
                })}
                {filteredFields.length === 0 && (
                  <tr>
                    <td colSpan={4} className="table-empty">
                      {combinedFields.length === 0 ? 'No fields extracted.' : 'No fields match the filter.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {(doc?.tables?.length ? doc.tables : lineItems ? [{
            name: 'line_items', columns: Array.from(new Set(lineItems.flatMap(item => Object.keys(item)))).map(key => ({key, label: key})),
            rows: lineItems.map(item => Object.entries(item).map(([column, value]) => ({column, value: String(value ?? '—'), confidence: 0, source_span: null}))),
          }] : []).map((table, tableIndex) => (
            <section key={`${table.name}-${tableIndex}`}>
              <div className="section-head"><h3>{table.name}</h3><span>{table.rows.length} rows · {table.columns.length} columns</span></div>
              <div className="table-wrap">
                <table className="fields-table line-items-table">
                  <thead><tr><th>#</th>{table.columns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead>
                  <tbody>{table.rows.map((row, index) => <tr key={index}>
                    <td>{index + 1}</td>
                    {table.columns.map(column => {
                      const cell = row.find(item => item.column === column.key);
                      return <td key={column.key} title={cell?.source_span || 'No cell evidence'}>
                        {String(cell?.value ?? '—')}
                      </td>;
                    })}
                  </tr>)}</tbody>
                </table>
              </div>
            </section>
          ))}
          {doc && <p className="box-text">Judge: {doc.judge_status || (doc.judge ? 'reviewed' : 'unavailable')}</p>}
          {group.response?.file_errors?.map(error => <p key={error} className="box-text">{error}</p>)}
          <div className="actions-row">
            <button className="button-secondary" onClick={() => navigator.clipboard.writeText(JSON.stringify(doc, null, 2))}>
              Copy Data
            </button>
            <button className="button-primary" onClick={() => downloadJson(doc, `${doc?.source?.filename || group.label}-page-${doc?.source?.page_number || docIndex + 1}`)}>
              Save JSON
            </button>
          </div>
        </>
      )}
    </>
  );

  return (
    <div className="extraction-layout with-preview">
      <div className="extraction-main">{fieldsPanel}</div>
      <aside className="source-panel">
        <h3 className="section-title flush-top">Source</h3>
        {imagePreviewUrl ? <a href={imagePreviewUrl} target="_blank" rel="noreferrer">
          <img src={imagePreviewUrl} alt={`Source page ${doc?.source?.page_number || docIndex + 1}`} className="source-image" />
        </a> : <p>Page preview unavailable for this saved result.</p>}
        <p>{doc?.source?.filename || group.label}{doc?.source && ` · Page ${doc.source.page_number}/${doc.source.page_count}`}</p>
        {doc?.source?.download_url && <a href={sourceUrl(doc.source.download_url)}>Download original</a>}
        {doc?.full_text && <details><summary>OCR text</summary><pre className="ocr-text">{doc.full_text}</pre></details>}
      </aside>
    </div>
  );
}
