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
  // OCR failure happens BEFORE the router: no pipeline step ran. Return -1
  // so no Router/Extractor step appears active after processing has ended;
  // the responsible stage (OCR) is shown explicitly via getFailedStageLabel.
  if (doc.failed_stage === 'ocr') return -1;
  if (doc.failed_stage) return STAGE_INDEX[doc.failed_stage] ?? 0;

  const hasFields = (doc.fields ?? []).length > 0;
  if (!hasFields) return 1;
  if (doc.completeness_score == null && (doc.validation_errors ?? []).length === 0 && !doc.judge) return 2;
  if (!doc.judge) return 3;
  return 4;
}

export function getFailedStageLabel(doc: ExtractionResult | null): string | null {
  if (!doc?.failed_stage) return null;
  const labels: Record<string, string> = {
    ocr: 'OCR',
    router: 'Router',
    extractor: 'Extractor',
    validator: 'Validator',
    judge: 'Judge',
  };
  return labels[doc.failed_stage] ?? doc.failed_stage;
}

export type ResultKind = 'technical_failure' | 'unreadable_source' | 'partial' | 'completed';

export function getResultKind(doc: ExtractionResult | null): ResultKind {
  if (!doc) return 'technical_failure';
  if (doc.error || doc.failed_stage) {
    // OCR incoherence / no readable text = unreadable source (honest block),
    // distinct from transport/timeout technical failures.
    const msg = `${doc.error ?? ''} ${(doc.validation_errors ?? []).join(' ')}`.toLowerCase();
    if (
      doc.failed_stage === 'ocr' ||
      msg.includes('incoherent') ||
      msg.includes('no readable text') ||
      msg.includes('could not be transcribed')
    ) {
      return 'unreadable_source';
    }
    return 'technical_failure';
  }
  if (doc.needs_review || (doc.acceptance_status && doc.acceptance_status !== 'accepted')) {
    return 'partial';
  }
  return 'completed';
}

export function mergeFieldValues(fields: CombinedField[]): Record<string, unknown> {
  return Object.fromEntries(fields.map(([name, field]) => [name, field.value]));
}

export function formatFieldValue(value: unknown, emptyFallback = ''): string {
  if (value === null || value === undefined) return emptyFallback;
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
