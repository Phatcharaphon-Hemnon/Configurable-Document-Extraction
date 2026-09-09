import { useCallback, useEffect, useRef, useState } from 'react';
import { extractFiles, pollJobStatus } from '../api/client';
import { pushToast } from '../lib/toast';
import type { DocumentGroup, ExtractionResult, FileExtractionResponse } from '../types/extraction';

function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function applyResult(
  group: DocumentGroup,
  response: FileExtractionResponse,
  patchGroup: (id: string, patch: Partial<DocumentGroup>) => void,
): void {
  patchGroup(group.id, { status: 'done', response });

  const docs: ExtractionResult[] = response.documents ?? [];
  const flagged = docs.filter((d) => d.needs_review).length;
  if (response.error) {
    pushToast('error', response.error, 7000);
  } else if (docs.length === 0) {
    pushToast('error', 'No documents detected in the upload.', 7000);
  } else if (flagged > 0) {
    pushToast('info', `${docs.length} document${docs.length > 1 ? 's' : ''} extracted — ${flagged} need${flagged > 1 ? '' : 's'} review.`);
  } else {
    pushToast('success', `${docs.length} document${docs.length > 1 ? 's' : ''} extracted successfully.`);
  }
}

export function useDocumentQueue() {
  const [groups, setGroups] = useState<DocumentGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedDocIndex, setSelectedDocIndex] = useState(0);
  // Guards against double-scheduling the same group (rapid Retry clicks):
  // only one pipeline run per group at a time.
  const processingRef = useRef<Set<string>>(new Set());
  const controllers = useRef(new Map<string, AbortController>());
  useEffect(() => {
    const active = controllers.current;
    return () => {
      active.forEach((controller) => controller.abort());
      active.clear();
    };
  }, []);

  const patchGroup = useCallback((id: string, patch: Partial<DocumentGroup>) => {
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, ...patch } : g)));
  }, []);

  const processGroup = useCallback(
    async (group: DocumentGroup) => {
      if (processingRef.current.has(group.id)) {
        pushToast('info', 'This upload is already processing — please wait.');
        return;
      }
      processingRef.current.add(group.id);
      const controller = new AbortController();
      controllers.current.set(group.id, controller);
      let jobId = group.jobId;
      let terminalFailure = false;
      patchGroup(group.id, { status: jobId ? 'queued' : 'uploading', error: undefined });

      try {
        // 202 immediately; the pipeline runs server-side. Polling survives
        // refresh/ retry churn: a duplicate upload reuses the running job.
        if (!jobId) {
          const accepted = await extractFiles(group.files, group.label, controller.signal);
          jobId = accepted.job_id;
          if (controller.signal.aborted) return;
          patchGroup(group.id, { jobId, status: 'queued' });
        }
        const job = await pollJobStatus(jobId, {
          signal: controller.signal,
          onStatus: (current) => {
            if (current.status === 'queued' || current.status === 'processing') {
              patchGroup(group.id, { status: current.status, progress: current.progress });
            }
          },
        });
        if (job.status === 'failed' || !job.result) {
          terminalFailure = true;
          throw new Error(job.error || 'Extraction failed.');
        }
        applyResult(group, job.result, patchGroup);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = jobId && !terminalFailure
          ? `Could not check job status. The job may still be running. ${toErrorMessage(err)}`
          : toErrorMessage(err);
        patchGroup(group.id, { status: 'error', error: message, jobId: terminalFailure ? undefined : jobId });
        pushToast('error', message, 8000);
      } finally {
        controllers.current.delete(group.id);
        processingRef.current.delete(group.id);
      }
    },
    [patchGroup],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;

      const label = files.length === 1 ? files[0].name : `${files.length} files (${files[0].name}, ...)`;
      const group: DocumentGroup = { id: crypto.randomUUID(), label, files, status: 'queued' };

      setGroups((prev) => [...prev, group]);
      setSelectedGroupId(group.id);
      setSelectedDocIndex(0);
      void processGroup(group);
    },
    [processGroup],
  );

  const retryGroup = useCallback(
    (id: string) => {
      const group = groups.find((g) => g.id === id);
      if (!group) return;
      if (group.status === 'uploading' || processingRef.current.has(id)) {
        pushToast('info', 'This upload is already processing — please wait.');
        return;
      }
      void processGroup(group);
    },
    [groups, processGroup],
  );

  const selectGroup = useCallback((id: string) => {
    setSelectedGroupId(id);
    setSelectedDocIndex(0);
  }, []);

  const selectedGroup = groups.find((g) => g.id === selectedGroupId) ?? null;
  const selectedDoc: ExtractionResult | null = selectedGroup?.response?.documents?.[selectedDocIndex] ?? null;

  return {
    groups,
    selectedGroupId,
    selectedGroup,
    selectedDocIndex,
    selectedDoc,
    selectDocIndex: setSelectedDocIndex,
    selectGroup,
    addFiles,
    retryGroup,
  };
}
