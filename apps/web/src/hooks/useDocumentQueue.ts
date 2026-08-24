import { useCallback, useState } from 'react';
import { extractFiles } from '../api/client';
import type { DocumentGroup, ExtractionResult } from '../types/extraction';

function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function useDocumentQueue() {
  const [groups, setGroups] = useState<DocumentGroup[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedDocIndex, setSelectedDocIndex] = useState(0);

  const patchGroup = useCallback((id: string, patch: Partial<DocumentGroup>) => {
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, ...patch } : g)));
  }, []);

  const processGroup = useCallback(
    async (group: DocumentGroup) => {
      patchGroup(group.id, { status: 'uploading' });

      try {
        const response = await extractFiles(group.files, group.label);
        patchGroup(group.id, { status: 'done', response });
      } catch (err) {
        patchGroup(group.id, { status: 'error', error: toErrorMessage(err) });
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
  };
}
