import { PipelineStepper } from './PipelineStepper';
import { AlertTriangleIcon, InfoIcon } from './icons';
import type { CombinedField, DocumentGroup, ExtractionResult } from '../types/extraction';
import { JUDGE_PASS_SCORE, formatFieldValue, getPipelineStage, mergeFieldValues } from '../utils/pipeline';

interface ExtractionTabProps {
  group: DocumentGroup | null;
  doc: ExtractionResult | null;
  docIndex: number;
  onSelectDoc: (index: number) => void;
  combinedFields: CombinedField[];
}

function copyToClipboard(fields: CombinedField[]) {
  navigator.clipboard.writeText(JSON.stringify(Object.fromEntries(fields), null, 2));
}

function downloadJson(fields: CombinedField[], label: string) {
  const blob = new Blob([JSON.stringify(Object.fromEntries(fields), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(label || 'document').replace(/\.[^/.]+$/, '')}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExtractionTab({ group, doc, docIndex, onSelectDoc, combinedFields }: ExtractionTabProps) {
  if (!group) {
    return <div className="empty-state">Select a document from the queue to view its extraction details.</div>;
  }

  if (group.status === 'error') {
    return (
      <div className="warning-box">
        <AlertTriangleIcon />
        <div>
          <h3>Upload Failed</h3>
          <p className="box-text">{group.error}</p>
        </div>
      </div>
    );
  }

  if (group.status !== 'done') {
    return <div className="empty-state">Processing document...</div>;
  }

  const documents = group.response?.documents ?? [];

  if (documents.length === 0) {
    return (
      <div className="warning-box">
        <AlertTriangleIcon />
        <div>
          <h3>No Documents Detected</h3>
          <p className="box-text">{group.response?.error || 'No readable pages were found in the uploaded file(s).'}</p>
        </div>
      </div>
    );
  }

  const validationErrors = doc?.validation?.issues?.filter((i) => i.severity === 'error') ?? [];
  const completeness = doc?.validation?.completeness_score;
  const stage = getPipelineStage(doc);

  return (
    <>
      {documents.length > 1 && (
        <div className="page-tabs">
          {documents.map((d, idx) => (
            <button key={d.id} className={idx === docIndex ? 'active' : ''} onClick={() => onSelectDoc(idx)}>
              Page {idx + 1}: {d.doc_type || 'unknown'}
            </button>
          ))}
        </div>
      )}

      <PipelineStepper stage={stage} />

      {doc?.error && (
        <div className="warning-box">
          <AlertTriangleIcon />
          <div>
            <h3>Processing Error</h3>
            <p className="box-text">{doc.error}</p>
          </div>
        </div>
      )}

      {!doc?.error && validationErrors.length > 0 && (
        <div className="info-box">
          <InfoIcon />
          <div>
            <h3 className="box-title-lg">Extraction Incomplete</h3>
            <p className="box-text spaced">The following issues were noted:</p>
            <ul className="box-list">
              {validationErrors.map((error, index) => (
                <li key={index}>
                  <strong>{error.field}:</strong> {error.message}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {!doc?.error && doc?.judge && (
        <div className={`${doc.judge.score < JUDGE_PASS_SCORE ? 'warning-box' : 'info-box'} judge-review`}>
          {doc.judge.score < JUDGE_PASS_SCORE ? <AlertTriangleIcon /> : null}
          <div>
            <h3 className="box-title-lg">Judge Review — {Math.round(doc.judge.score * 100)}% confidence</h3>
            {doc.judge.notes && <p className="box-text spaced">{doc.judge.notes}</p>}
            {doc.judge.issues.length > 0 && (
              <>
                <p className="box-text spaced">Flagged fields:</p>
                <ul className="box-list">
                  {doc.judge.issues.map((issue, index) => (
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

      {!doc?.error && (
        <>
          <div className="completeness-row">
            <span className="hint" style={{ margin: 0 }}>
              Field completeness:
            </span>
            <div className="confidence-bar-container completeness-bar">
              <div className="confidence-bar" style={{ width: `${(completeness ?? 0) * 100}%` }} />
            </div>
            <span className="box-text">{Math.round((completeness ?? 0) * 100)}% complete</span>
          </div>

          <h3 className="section-title">Extracted Fields</h3>
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
                  <td>{key}</td>
                  <td>{formatFieldValue(field.value)}</td>
                  <td>
                    <div className="confidence-bar-container">
                      <div className="confidence-bar" style={{ width: `${(field.confidence || 0) * 100}%` }} />
                    </div>
                  </td>
                  <td className="source-span-cell">{field.source_span || 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>

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
}
