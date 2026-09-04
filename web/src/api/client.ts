import type { ApiRoot, EvaluateResponse, JobAcceptedResponse, JobStatusResponse } from '../types/extraction';

// Public by design (no secrets): points at the FastAPI backend.
// Defaults to the relative "/api" — the Vite dev proxy handles local dev,
// and same-origin deployments (e.g. a reverse proxy in front of both apps)
// work with zero configuration.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

async function requestJson<T>(url: string, init: RequestInit | undefined, fallbackError: string): Promise<T> {
  const res = await fetch(url, init);
  // Read text first: a dropped connection can resolve with an EMPTY body,
  // and res.json() on that throws a cryptic SyntaxError. Surface the
  // meaningful fallback instead.
  const text = await res.text();
  if (!text) {
    throw new Error(res.ok ? fallbackError : `Request failed with status ${res.status}`);
  }
  let payload: (T & { detail?: string }) | null = null;
  try {
    payload = JSON.parse(text) as T & { detail?: string };
  } catch {
    throw new Error(fallbackError);
  }

  if (!res.ok) {
    throw new Error(payload?.detail ?? fallbackError);
  }
  return payload;
}

export async function fetchApiRoot(): Promise<ApiRoot | null> {
  try {
    return await requestJson<ApiRoot>(`${API_BASE_URL}/`, undefined, 'Failed to load API root');
  } catch {
    return null;
  }
}

export function extractFiles(files: File[], label: string): Promise<JobAcceptedResponse> {
  const form = new FormData();
  for (const file of files) {
    form.append('files', file);
  }
  // 202 Accepted: extraction runs in the background; poll getJobStatus.
  return requestJson(`${API_BASE_URL}/extract`, { method: 'POST', body: form }, `Extract failed for ${label}`);
}

export function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  return requestJson(`${API_BASE_URL}/jobs/${jobId}`, undefined, `Failed to load job ${jobId}`);
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function pollJobStatus(jobId: string): Promise<JobStatusResponse> {
  const started = Date.now();
  for (;;) {
    const job = await getJobStatus(jobId);
    if (job.status === 'completed' || job.status === 'failed') {
      return job;
    }
    if (Date.now() - started > POLL_TIMEOUT_MS) {
      throw new Error('Extraction is taking too long — check the History tab for its status.');
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

export type EvaluatePayload = {
  doc_type: string;
  prediction: Record<string, unknown>;
  ground_truth: Record<string, unknown>;
};

export function evaluateExtraction(payload: EvaluatePayload): Promise<EvaluateResponse> {
  return requestJson(
    `${API_BASE_URL}/evaluate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    'Evaluation failed',
  );
}
