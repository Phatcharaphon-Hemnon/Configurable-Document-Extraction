import type { CombinedField, ExtractionResult } from '../types/extraction';

export const PIPELINE_STEPS = ['Router', 'Extractor', 'Validator', 'Judge'] as const;

export const JUDGE_PASS_SCORE = 0.7;

const STAGE_INDEX: Record<string, number> = {
  router: 0,
  extractor: 1,
  validator: 2,
  judge: 3,
};

export function getPipelineStage(doc: ExtractionResult | null): number {
  if (!doc) return 0;
  if (doc.failed_stage) return STAGE_INDEX[doc.failed_stage] ?? 0;
  if (doc.doc_type == null) return 0;

  const hasExtracted = Object.keys(doc.extracted_fields || {}).length > 0;
  const hasAdditional = Object.keys(doc.additional_fields || {}).length > 0;
  if (!hasExtracted && !hasAdditional) return 1;

  if (doc.validation == null) return 2;
  if (doc.judge == null) return 3;
  return 4;
}

export function mergeFieldValues(fields: CombinedField[]): Record<string, unknown> {
  return Object.fromEntries(fields.map(([name, field]) => [name, field.value]));
}

export function formatFieldValue(value: unknown, emptyFallback = ''): string {
  return typeof value === 'object' ? JSON.stringify(value) : String(value ?? emptyFallback);
}
