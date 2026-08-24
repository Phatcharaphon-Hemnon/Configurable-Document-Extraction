export type GroupStatus = 'queued' | 'uploading' | 'done' | 'error';

export type ExtractionField = {
  value: unknown;
  confidence: number;
  source_span?: string | null;
  likely_required?: boolean;
};

export type ValidationIssue = { field: string; message: string; severity: string };

export type ValidationResult = { is_valid: boolean; completeness_score: number; issues: ValidationIssue[] };

export type JudgeIssue = { field: string; message: string; severity: string };

export type JudgeResult = { score: number; issues: JudgeIssue[]; notes: string };

export type SuggestedField = { name: string; description?: string | null; likely_required: boolean };

export type FailedStage = 'router' | 'extractor' | 'validator' | 'judge';

export type ExtractionResult = {
  id: string;
  doc_type: string | null;
  language: string | null;
  routing_reason?: string | null;
  suggested_fields: SuggestedField[];
  extracted_fields: Record<string, ExtractionField>;
  additional_fields: Record<string, ExtractionField>;
  validation: ValidationResult | null;
  judge: JudgeResult | null;
  needs_review: boolean;
  error?: string | null;
  failed_stage?: FailedStage | null;
};

export type FileExtractionResponse = {
  request: { filename: string; content_type?: string | null; size_bytes?: number | null };
  documents: ExtractionResult[];
  error?: string | null;
};

export type EvaluateMismatch = { field: string; predicted?: unknown; expected?: unknown };

export type EvaluateResponse = {
  score: number;
  precision: number;
  recall: number;
  f1: number;
  summary: string;
  mismatches: EvaluateMismatch[];
};

export type DocumentGroup = {
  id: string;
  label: string;
  files: File[];
  status: GroupStatus;
  response?: FileExtractionResponse;
  error?: string;
};

export type ApiRoot = {
  recommended_extraction_model?: { display_name: string; reason: string };
};

export type CombinedField = [name: string, field: ExtractionField];
