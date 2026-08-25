import { useEffect, useMemo, useState } from 'react';
import { PipelineStepper } from './PipelineStepper';
import { AlertTriangleIcon, CheckCircleIcon } from './icons';
import { JUDGE_PASS_SCORE, formatFieldValue, getPipelineStage, mergeFieldValues } from '../utils/pipeline';
import type { CombinedField, DocumentGroup, ExtractionResult } from '../types/extraction';

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

function useImagePreview(group: DocumentGroup | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!group || group.status !== 'done') {
      setUrl(null);
      return;
    }
    const imageFile = group.files.find((f) => f.type.startsWith('image/'));
    if (!imageFile) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(imageFile);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [group]);

  return url;
}

function copyToClipboard(fields: CombinedField[]) {
  navigator.clipboard.writeText(JSON.stringify(mergeFieldValues(fields), null, 2));
}

function downloadJson(fields: CombinedField[], label: string) {
  const blob = new Blob([JSON.stringify(mergeFieldValues(fields), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(label || 'document').replace(/\.[^/.]+$/, '')}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExtractionTab({ group, doc, docIndex, onSelectDoc, onRetry, combinedFields }: ExtractionTabProps) {
  const [filter, setFilter] = useState('');
  const imagePreviewUrl = useImagePreview(group);

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
          <h3>Upload Failed</h3>
          <p className="box-text">{group.error}</p>
          <button className="button-secondary retry-button" onClick={() => onRetry(group.id)}>
            Retry Upload
          </button>
        </div>
      </div>
    );
  }

  if (group.status !== 'done') {
    return (
      <div className="empty-state">
        <p>Processing document…</p>
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
            Retry Upload
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
            <span className="completeness-label">Field completeness</span>
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

          {lineItems && (
            <>
              <div className="section-head">
                <h3 className="section-title">Line Items</h3>
                <span className="field-count">{lineItems.length} rows</span>
              </div>
              <div className="table-wrap">
                <table className="fields-table line-items-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Item</th>
                      <th>Qty</th>
                      <th>Unit Price</th>
                      <th>Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineItems.map((item, idx) => (
                      <tr key={idx}>
                        <td className="line-item-num">{idx + 1}</td>
                        <td className="field-value">{String(item.name ?? item.description ?? '—')}</td>
                        <td>{String(item.quantity ?? '—')}</td>
                        <td>{String(item.unit_price ?? '—')}</td>
                        <td>{String(item.total_price ?? item.amount ?? '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="actions-row">
            <button className="button-secondary" onClick={() => copyToClipboard(combinedFields)}>
              Copy Data
            </button>
            <button className="button-primary" onClick={() => downloadJson(combinedFields, group.label)}>
              Save JSON
            </button>
          </div>
        </>
      )}
    </>
  );

  return (
    <div className={imagePreviewUrl ? 'extraction-layout with-preview' : 'extraction-layout'}>
      <div className="extraction-main">{fieldsPanel}</div>
      {imagePreviewUrl && (
        <aside className="source-panel">
          <h3 className="section-title flush-top">Source</h3>
          <a href={imagePreviewUrl} target="_blank" rel="noreferrer" title="Open full size">
            <img src={imagePreviewUrl} alt="Uploaded document" className="source-image" />
          </a>
          <p className="source-caption">{group.label}</p>
        </aside>
      )}
    </div>
  );
}
