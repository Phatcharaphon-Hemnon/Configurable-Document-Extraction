import { AlertTriangleIcon } from './icons';
import { useEvaluation } from '../hooks/useEvaluation';
import type { CombinedField, ExtractionResult } from '../types/extraction';
import { formatFieldValue } from '../utils/pipeline';

interface EvaluationTabProps {
  groupId: string | null;
  docIndex: number;
  doc: ExtractionResult | null;
  combinedFields: CombinedField[];
}

export function EvaluationTab({ groupId, docIndex, doc, combinedFields }: EvaluationTabProps) {
  const evalKey = groupId && doc ? `${groupId}:${docIndex}` : null;
  const {
    draft,
    setDraft,
    evaluation,
    error,
    isEvaluating,
    handleGroundTruthFile,
    prefillFromExtracted,
    runEvaluation,
  } = useEvaluation({ evalKey, docType: doc?.doc_type ?? null, combinedFields });

  if (!doc) {
    return <div className="empty-state">Select a document from the queue to evaluate its extraction.</div>;
  }

  const canRun = !isEvaluating && draft.trim().length > 0;

  return (
    <div className="evaluation-tab-content">
      <h3 className="section-title flush-top">Evaluation Metrics</h3>

      <p className="hint">
        Paste or upload the expected (ground truth) field values as JSON to score this document's extracted fields
        against them.
      </p>

      <div className="controls-row">
        <label className="pill-button">
          Upload Ground Truth JSON
          <input type="file" accept="application/json,.json" className="hidden-file-input" onChange={handleGroundTruthFile} />
        </label>
        <button className="pill-button" onClick={prefillFromExtracted}>
          Prefill From Extracted Fields
        </button>
      </div>

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={'{\n  "invoice_number": "INV-001",\n  "total": "1234.56"\n}'}
        rows={10}
        className="ground-truth-input"
      />

      {error && (
        <div className="warning-box flush-warning">
          <AlertTriangleIcon />
          <div>
            <p className="box-text">{error}</p>
          </div>
        </div>
      )}

      <div className="run-row">
        <button className="button-primary" onClick={runEvaluation} disabled={!canRun}>
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

          <p className="hint spaced">{evaluation.summary}</p>

          {evaluation.mismatches.length > 0 && (
            <>
              <h3 className="section-title">Mismatches ({evaluation.mismatches.length})</h3>
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
                      <td>{m.field}</td>
                      <td>{formatFieldValue(m.predicted, '—')}</td>
                      <td>{formatFieldValue(m.expected, '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  );
}
