export type DocType = 'invoice' | 'purchase_order' | 'delivery_note';

export type GroupStatus = 'queued' | 'uploading' | 'processing' | 'done' | 'error';

export type AcceptanceStatus = 'accepted' | 'rejected' | 'unresolved' | 'unevaluated';

export type EvidenceReference = {
  block_id?: string | null;
  page_number: number;
  subspan?: string | null;
  box?: [number, number, number, number] | null;
  engine?: string | null;
  role?: 'label' | 'value' | 'context' | null;
};

export type ExtractedField = {
  name: string;
  value: string | number | null;
  confidence: number;
  source_span?: string | null;
  is_new_field?: boolean;
  evidence_refs?: EvidenceReference[];
  acceptance?: AcceptanceStatus;
};

export type RejectedCandidate = {
  candidate_id: string;
  kind: 'field' | 'cell' | 'row' | 'table';
  location: string;
  proposed_value?: string | number | null;
  confidence: number;
  raw_evidence?: string | null;
  source_refs?: EvidenceReference[];
  rejection_reason: string;
  validation_findings?: string[];
};

export type StructuredReviewIssue = {
  category: 'mechanical' | 'unsupported' | 'row_column' | 'type' | 'semantic' | 'ocr_ambiguity';
  target: string;
  severity: 'info' | 'warning' | 'error';
  evidence?: string | null;
  explanation: string;
};

export type ResultCacheMetadata = {
  fingerprint: string;
  computed_at?: string | null;
  original_timings?: Record<string, number>;
  acceptance_policy_version?: string;
  cache_lookup_ms?: number;
  hit_type: 'full' | 'partial' | 'miss';
};

export type JudgeIssue = {
  field: string; message: string; severity: string;
  category?: string | null; target?: string | null;
  evidence?: string | null; explanation?: string | null;
};

export type JudgeResult = { score: number; issues: JudgeIssue[]; notes: string };

export type ProviderErrorDetails = {
  stage?: string | null;
  provider?: string | null;
  model?: string | null;
  error_type?: string | null;
  status?: number | null;
  code?: string | null;
  param?: string | null;
  type?: string | null;
  message?: string | null;
  request_id?: string | null;
};

export type TableCell = {
  column: string; value: string | number | null; confidence: number;
  source_span?: string | null; evidence_refs?: EvidenceReference[];
  acceptance?: AcceptanceStatus;
};

export type ExtractedTable = {
  name: string;
  columns: {key: string; label: string}[];
  rows: TableCell[][];
};
export type SourceReference = {
  source_id: string; filename: string; page_number: number; page_count: number;
  preview_url?: string | null; download_url?: string | null;
};
export type JobProgress = {completed_pages: number; total_pages: number; stage: string; queue_seconds: number};

export type ExtractionResult = {
  source?: SourceReference | null;
  tables?: ExtractedTable[];
  timings?: Record<string, number>;
  judge_status?: 'passed' | 'flagged' | 'skipped' | 'unavailable';
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
  failed_stage?: 'ocr' | 'router' | 'extractor' | 'validator' | 'judge' | null;
  error_details?: ProviderErrorDetails | null;
  extraction_source?: 'vision' | 'ocr' | 'text' | null;
  auto_evaluation?: EvaluateResponse | null;
  rejected_candidates?: RejectedCandidate[];
  review_issues?: StructuredReviewIssue[];
  acceptance_status?: AcceptanceStatus;
  acceptance_policy_version?: string;
  cache_metadata?: ResultCacheMetadata | null;
};

export type FileUploadMeta = {
  filename: string;
  content_type?: string | null;
  size_bytes?: number | null;
};

export type FileExtractionResponse = {
  request: FileUploadMeta;
  documents: ExtractionResult[];
  file_errors?: string[];
  timings?: Record<string, number>;
  error?: string | null;
  job_id?: string | null;
};

export type JobAcceptedResponse = {
  job_id: string;
  status: string;
};

export type JobStatusResponse = {
  progress?: JobProgress | null;
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
  progress?: JobProgress | null;
  readOnly?: boolean;
  id: string;
  label: string;
  files: File[];
  jobId?: string;
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
