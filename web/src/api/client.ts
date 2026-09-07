import { pollUntilTerminal, type PollOptions } from './jobPolling';
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

export function extractFiles(files: File[], label: string, signal?: AbortSignal): Promise<JobAcceptedResponse> {
  const form = new FormData();
  for (const file of files) {
    form.append('files', file);
  }
  // 202 Accepted: extraction runs in the background; poll getJobStatus.
  return requestJson(`${API_BASE_URL}/extract`, { method: 'POST', body: form, signal }, `Extract failed for ${label}`);
}

export function getJobStatus(jobId: string, signal?: AbortSignal): Promise<JobStatusResponse> {
  const timeout = AbortSignal.timeout(30000);
  return requestJson(`${API_BASE_URL}/jobs/${jobId}`, {
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  }, `Failed to load job ${jobId}`);
}

export function pollJobStatus(jobId: string, options: PollOptions = {}): Promise<JobStatusResponse> {
  return pollUntilTerminal((signal) => getJobStatus(jobId, signal), options);
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
