import type { JobStatusResponse } from '../types/extraction';

export type PollOptions = {
  signal?: AbortSignal;
  onStatus?: (job: JobStatusResponse) => void;
};

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

// Queue time is unbounded; only a terminal server status ends monitoring.
export async function pollUntilTerminal(
  getStatus: (signal?: AbortSignal) => Promise<JobStatusResponse>,
  { signal, onStatus }: PollOptions = {},
): Promise<JobStatusResponse> {
  for (;;) {
    signal?.throwIfAborted();
    const job = await getStatus(signal);
    signal?.throwIfAborted();
    onStatus?.(job);
    if (job.status === 'completed' || job.status === 'failed') return job;
    if (job.status !== 'queued' && job.status !== 'processing') {
      throw new Error(`Unexpected job status: ${job.status}`);
    }
    await sleep(5000, signal);
  }
}
