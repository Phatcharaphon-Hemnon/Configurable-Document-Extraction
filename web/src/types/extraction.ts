export type DocType = 'invoice' | 'purchase_order' | 'delivery_note';

export type GroupStatus = 'queued' | 'uploading' | 'done' | 'error';

export type ExtractedField = {
  name: string;
  value: string | number | null;
  confidence: number;
  source_span?: string | null;
  is_new_field?: boolean;
};

export type JudgeIssue = { field: string; message: string; severity: string };

export type JudgeResult = { score: number; issues: JudgeIssue[]; notes: string };

export type ExtractionResult = {
  id: string;
  doc_type: DocType;
  language: string | null;
  fields: ExtractedField[];
  validation_errors: string[];
  needs_review: boolean;
  completeness_score: number;
  judge: JudgeResult | null;
  routing_reason?: string | null;
  full_text?: string | null;
  error?: string | null;
  failed_stage?: 'router' | 'extractor' | 'validator' | 'judge' | null;
  extraction_source?: 'vision' | 'ocr' | 'text' | null;
  auto_evaluation?: EvaluateResponse | null;
};

export type FileUploadMeta = {
  filename: string;
  content_type?: string | null;
  size_bytes?: number | null;
};

export type FileExtractionResponse = {
  request: FileUploadMeta;
  documents: ExtractionResult[];
  error?: string | null;
  job_id?: string | null;
};

export type JobAcceptedResponse = {
  job_id: string;
  status: string;
};

export type JobStatusResponse = {
  job_id: string;
  status: string;
  result?: FileExtractionResponse | null;
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
  doc_types?: string[];
  langfuse_enabled?: boolean;
  temporal_enabled?: boolean;
  extraction_model?: string;
};

export type CombinedField = [name: string, field: ExtractedField];
