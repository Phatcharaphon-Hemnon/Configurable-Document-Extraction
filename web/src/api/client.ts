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

export function extractFiles(
  files: File[],
  label: string,
  signal?: AbortSignal,
  opts?: { forceRefresh?: boolean; disableCaches?: boolean },
): Promise<JobAcceptedResponse> {
  const form = new FormData();
  for (const file of files) {
    form.append('files', file);
  }
  const params = new URLSearchParams();
  if (opts?.forceRefresh) params.set('force_refresh', 'true');
  if (opts?.disableCaches) params.set('disable_caches', 'true');
  const query = params.toString() ? `?${params.toString()}` : '';
  // 202 Accepted: extraction runs in the background; poll getJobStatus.
  // force_refresh bypasses the completed-result cache (OCR cache stays on);
  // disable_caches bypasses both (benchmark/debug).
  return requestJson(`${API_BASE_URL}/extract${query}`, { method: 'POST', body: form, signal }, `Extract failed for ${label}`);
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

export function sourceUrl(path?: string | null): string | undefined {
  return path ? `${API_BASE_URL.replace(/\/$/, '')}${path}` : undefined;
}

// Download a stored original for History retry. Throws an actionable error
// when the backend no longer has the bytes (history cleared, sources pruned,
// or DB reset) so the UI can tell the user to re-upload instead of spinning.
export async function downloadOriginal(downloadPath: string, filename: string): Promise<File> {
  const url = sourceUrl(downloadPath);
  if (!url) throw new Error(`Original unavailable for ${filename} — re-upload the file to retry.`);
  const res = await fetch(url);
  if (res.status === 404) {
    throw new Error(
      `Original file for ${filename} is no longer stored (history was cleared or sources were removed). Re-upload the file to retry extraction.`,
    );
  }
  if (!res.ok) {
    throw new Error(`Could not download original for ${filename} (HTTP ${res.status}). Re-upload the file to retry.`);
  }
  const blob = await res.blob();
  if (blob.size === 0) {
    throw new Error(`Downloaded original for ${filename} is empty — re-upload the file to retry.`);
  }
  return new File([blob], filename, { type: blob.type || 'application/octet-stream' });
}
