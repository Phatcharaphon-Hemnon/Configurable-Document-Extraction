import { useEffect, useMemo, useState } from 'react';
import { sourceUrl } from '../api/client';
import { PipelineStepper } from './PipelineStepper';
import { AlertTriangleIcon, CheckCircleIcon } from './icons';
import { JUDGE_PASS_SCORE, formatFieldValue, getFailedStageLabel, getPipelineStage, getResultKind } from '../utils/pipeline';
import type { CombinedField, DocumentGroup, ExtractionResult, ProviderErrorDetails } from '../types/extraction';

interface ExtractionTabProps {
  group: DocumentGroup | null;
  doc: ExtractionResult | null;
  docIndex: number;
  onSelectDoc: (index: number) => void;
  onRetry: (id: string, opts?: { forceRefresh?: boolean; disableCaches?: boolean }) => void;
  combinedFields: CombinedField[];
}

const PLACEHOLDER_VALUES = new Set([
  '', 'n/a', 'na', '-', '--', '—', '–', 'null', 'none', 'nil',
  'not available', 'unknown', 'tbd', 'blank', 'empty',
  'ไม่มีข้อมูล', 'ไม่ระบุ', 'ไม่มี',
]);

function isPlaceholderValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value !== 'string') return false;
  return PLACEHOLDER_VALUES.has(value.trim().toLowerCase().replace(/_/g, ' '));
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

  // Reset the field filter when switching pages/documents (avoid stale state).
  useEffect(() => {
    setFilter('');
  }, [doc?.id]);

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
        {/* Stepper = stages executed, NOT data correctness. Data status is separate below. */}
        <div className="doc-meta-stepper" title="Pipeline stages executed (not data correctness)">
          <PipelineStepper stage={getPipelineStage(doc)} failedStage={doc?.failed_stage ?? null} />
        </div>
        {doc?.failed_stage && (
          <span className="meta-chip" title="Responsible stage for this terminal outcome">
            failed at {getFailedStageLabel(doc) ?? doc.failed_stage} · {getResultKind(doc).replace(/_/g, ' ')}
          </span>
        )}
        {!doc?.failed_stage && doc && (
          <span className="meta-chip" title="Terminal outcome kind">
            {getResultKind(doc).replace(/_/g, ' ')}
          </span>
        )}
      </div>
      {!doc?.error && doc && (
        <div className="doc-meta-row" style={{ gap: '8px', flexWrap: 'wrap' }}>
          <span className="meta-chip" title="Acceptance policy outcome for this page">
            Data: {doc.acceptance_status === 'accepted' ? (doc.needs_review ? 'accepted · needs review' : 'accepted')
              : doc.acceptance_status === 'unresolved' ? 'unresolved'
              : doc.acceptance_status === 'rejected' ? 'rejected'
              : 'legacy · unevaluated'}
          </span>
          {doc.acceptance_policy_version && (
            <span className="meta-chip" title="Deterministic acceptance-policy version">policy {doc.acceptance_policy_version}</span>
          )}
          {doc.cache_metadata && (
            <span className="meta-chip" title={`Cached result from ${doc.cache_metadata.computed_at ?? 'earlier'} (fingerprint ${doc.cache_metadata.fingerprint.slice(0, 12)}…)`}>
              cache hit
            </span>
          )}
          {doc.needs_review
            ? <span className="meta-chip" style={{ borderColor: '#4D1717' }}>needs review — stage checkmarks mean “executed”, not “correct”</span>
            : <span className="meta-chip">review clear</span>}
        </div>
      )}

      {doc?.error && (
        <div className="callout callout-danger">
          <AlertTriangleIcon />
          <div className="callout-body">
            <h3>
              {doc.failed_stage === 'ocr'
                ? 'OCR failed — unreadable source (no extraction attempted)'
                : doc.failed_stage
                  ? `${getFailedStageLabel(doc) ?? doc.failed_stage} failed — ${getResultKind(doc).replace(/_/g, ' ')}`
                  : 'Processing Error'}
            </h3>
            <p className="box-text">{doc.error}</p>
            {doc.error_details && <ProviderErrorBlock details={doc.error_details} />}
            {group && (
              <div style={{ marginTop: '8px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  className="button-secondary retry-button"
                  title="Bypass the completed-result cache (OCR cache stays on)"
                  onClick={() => onRetry(group.id, { forceRefresh: true })}
                >
                  Retry (force refresh)
                </button>
                <button
                  className="button-secondary retry-button"
                  title="Bypass both result and OCR caches (debug)"
                  onClick={() => onRetry(group.id, { disableCaches: true })}
                >
                  Retry uncached
                </button>
              </div>
            )}
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
                    <strong>{issue.target || issue.field}</strong> [{issue.category || 'review'}/{issue.severity}]: {issue.explanation || issue.message}
                    {issue.evidence && <span className="box-text"> — evidence: {issue.evidence}</span>}
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
                  <th title="Model-estimated confidence, not measured accuracy">Confidence (model estimate)</th>
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
                        {field.acceptance && field.acceptance !== 'accepted' && (
                          <span className="new-field-tag" title="Acceptance status">{field.acceptance}</span>
                        )}
                        {(!field.acceptance || (doc?.acceptance_status === 'unevaluated')) && (
                          <span className="new-field-tag" title="Saved before acceptance policy — not retroactively accepted">legacy</span>
                        )}
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

          {(() => {
            // Validated tables only. The legacy scalar fallback never fabricates
            // placeholder rows and is labeled legacy-unevaluated.
            type FallbackTable = {
              name: string;
              columns: { key: string; label: string }[];
              rows: { column: string; value: string | number | null; confidence: number; source_span: string | null }[][];
              legacy: boolean;
            };
            const validated: FallbackTable[] = (doc?.tables ?? []).map(t => ({
              name: t.name,
              columns: t.columns.map(c => ({ key: c.key, label: c.label })),
              rows: t.rows.map(r => r.map(cell => ({
                column: cell.column,
                value: cell.value,
                confidence: cell.confidence,
                source_span: cell.source_span ?? null,
              }))),
              legacy: false,
            }));
            const fallback: FallbackTable[] = (!validated.length && lineItems) ? [{
              name: 'line_items',
              columns: Array.from(new Set(lineItems.flatMap(item => Object.keys(item)))).map(key => ({ key, label: key })),
              rows: lineItems
                .filter(item => Object.values(item).some(v => !isPlaceholderValue(v)))
                .map(item => Object.entries(item)
                  .filter(([, value]) => !isPlaceholderValue(value))
                  .map(([column, value]) => ({ column, value: String(value ?? '—'), confidence: 0, source_span: null })))
                .filter(row => row.length > 0),
              legacy: true,
            }] : [];
            const tables: FallbackTable[] = validated.length ? validated : fallback;
            return tables.map((table, tableIndex: number) => (
              <section key={`${table.name}-${tableIndex}`}>
                <div className="section-head">
                  <h3>{table.name}{table.legacy ? ' (legacy scalar fallback · unevaluated)' : ''}</h3>
                  {/* Counts derive from the same validated structure rendered below. */}
                  <span>{table.rows.length} rows · {table.columns.length} columns</span>
                </div>
                <div className="table-wrap">
                  <table className="fields-table line-items-table">
                    <thead><tr><th>#</th>{table.columns.map((column: { key: string; label: string }) => <th key={column.key}>{column.label}</th>)}</tr></thead>
                    <tbody>{table.rows.map((row: FallbackTable['rows'][number], index: number) => <tr key={index}>
                      <td>{index + 1}</td>
                      {table.columns.map((column: { key: string; label: string }) => {
                        const cell = row.find((item: { column: string }) => item.column === column.key);
                        return <td key={column.key} title={cell?.source_span || 'No cell evidence'}>
                          {String(cell?.value ?? '—')}
                        </td>;
                      })}
                    </tr>)}</tbody>
                  </table>
                </div>
              </section>
            ));
          })()}
          {doc && (
            <p className="box-text">
              Judge: {doc.judge_status || (doc.judge ? 'reviewed' : 'unavailable')}
              {(doc.judge_status === 'skipped' || doc.judge_status === 'unavailable') ? ' (not passed)' : ''}
            </p>
          )}
          {doc?.auto_evaluation && (
            <details style={{ marginTop: '8px' }}>
              <summary className="box-text" style={{ cursor: 'pointer' }}>
                Measured accuracy (ground-truth auto-eval, separate from review): F1 {(doc.auto_evaluation.f1 * 100).toFixed(1)}%
              </summary>
              <p className="box-text">
                Precision {(doc.auto_evaluation.precision * 100).toFixed(1)}% · Recall {(doc.auto_evaluation.recall * 100).toFixed(1)}% ·
                review outcome above is not an accuracy score.
              </p>
            </details>
          )}
          {(doc?.rejected_candidates?.length ?? 0) > 0 && (
            <details style={{ marginTop: '8px' }} open={false}>
              <summary className="box-text" style={{ cursor: 'pointer' }}>
                Rejected for review ({doc?.rejected_candidates?.length}): excluded from accepted data & exports
              </summary>
              <ul className="box-list">
                {doc?.rejected_candidates?.map(rej => (
                  <li key={rej.candidate_id}>
                    <strong>{rej.location}</strong> [{rej.kind}]: {String(rej.proposed_value ?? '—')} — {rej.rejection_reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(doc?.review_issues?.length ?? 0) > 0 && (
            <details style={{ marginTop: '8px' }}>
              <summary className="box-text" style={{ cursor: 'pointer' }}>
                Structured review issues ({doc?.review_issues?.length})
              </summary>
              <ul className="box-list">
                {doc?.review_issues?.map((issue, i) => (
                  <li key={i}>
                    <strong>{issue.target}</strong> [{issue.category}/{issue.severity}]: {issue.explanation}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {group.response?.file_errors?.map(error => <p key={error} className="box-text">{error}</p>)}
          <div className="actions-row">
            <button className="button-secondary" onClick={() => navigator.clipboard.writeText(JSON.stringify(doc, null, 2))}>
              Copy Data
            </button>
            <button className="button-secondary" title="Bypass the completed-result cache (OCR cache stays on)" onClick={() => group && onRetry(group.id, { forceRefresh: true })}>
              Force refresh
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
