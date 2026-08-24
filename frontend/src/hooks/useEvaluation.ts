import { useCallback, useState, type ChangeEvent } from 'react';
import { evaluateExtraction } from '../api/client';
import type { CombinedField, EvaluateResponse } from '../types/extraction';

const INVALID_JSON_ERROR = 'Ground truth must be valid JSON, e.g. {"invoice_number": "INV-001"}.';

interface UseEvaluationArgs {
  evalKey: string | null;
  docType: string | null;
  combinedFields: CombinedField[];
}

export function useEvaluation({ evalKey, docType, combinedFields }: UseEvaluationArgs) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [evaluations, setEvaluations] = useState<Record<string, EvaluateResponse>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Record<string, boolean>>({});

  const draft = evalKey ? (drafts[evalKey] ?? '') : '';
  const evaluation = evalKey ? evaluations[evalKey] : undefined;
  const error = evalKey ? errors[evalKey] : undefined;
  const isEvaluating = evalKey ? !!pending[evalKey] : false;

  const setDraft = useCallback(
    (value: string) => {
      if (!evalKey) return;
      setDrafts((prev) => ({ ...prev, [evalKey]: value }));
    },
    [evalKey],
  );

  const handleGroundTruthFile = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file || !evalKey) return;

      const reader = new FileReader();
      reader.onload = () => setDrafts((prev) => ({ ...prev, [evalKey]: String(reader.result ?? '') }));
      reader.readAsText(file);
      event.target.value = '';
    },
    [evalKey],
  );

  const prefillFromExtracted = useCallback(() => {
    if (!evalKey) return;
    const merged = Object.fromEntries(combinedFields.map(([name, field]) => [name, field.value]));
    setDrafts((prev) => ({ ...prev, [evalKey]: JSON.stringify(merged, null, 2) }));
  }, [evalKey, combinedFields]);

  const clearError = useCallback(() => {
    if (!evalKey) return;
    setErrors((prev) => {
      const next = { ...prev };
      delete next[evalKey];
      return next;
    });
  }, [evalKey]);

  const runEvaluation = useCallback(async () => {
    if (!evalKey) return;

    let groundTruth: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(drafts[evalKey] ?? '');
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('not an object');
      }
      groundTruth = parsed as Record<string, unknown>;
    } catch {
      setErrors((prev) => ({ ...prev, [evalKey]: INVALID_JSON_ERROR }));
      return;
    }

    clearError();
    setPending((prev) => ({ ...prev, [evalKey]: true }));

    const prediction = Object.fromEntries(combinedFields.map(([name, field]) => [name, field.value]));

    try {
      const payload = await evaluateExtraction({
        doc_type: docType || 'unknown',
        prediction,
        ground_truth: groundTruth,
      });
      setEvaluations((prev) => ({ ...prev, [evalKey]: payload }));
    } catch (err) {
      setErrors((prev) => ({
        ...prev,
        [evalKey]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setPending((prev) => ({ ...prev, [evalKey]: false }));
    }
  }, [evalKey, drafts, combinedFields, docType, clearError]);

  return {
    draft,
    setDraft,
    evaluation,
    error,
    isEvaluating,
    handleGroundTruthFile,
    prefillFromExtracted,
    runEvaluation,
  };
}
