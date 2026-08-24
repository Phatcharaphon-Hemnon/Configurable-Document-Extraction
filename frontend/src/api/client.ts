import type { ApiRoot, EvaluateResponse, FileExtractionResponse } from '../types/extraction';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

async function requestJson<T>(url: string, init: RequestInit | undefined, fallbackError: string): Promise<T> {
  const res = await fetch(url, init);
  const payload = (await res.json()) as T & { detail?: string };

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

export function extractFiles(files: File[], label: string): Promise<FileExtractionResponse> {
  const form = new FormData();
  for (const file of files) {
    form.append('files', file);
  }
  return requestJson(`${API_BASE_URL}/extract`, { method: 'POST', body: form }, `Extract failed for ${label}`);
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
