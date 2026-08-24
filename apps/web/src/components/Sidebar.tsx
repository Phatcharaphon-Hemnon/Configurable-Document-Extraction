import { useRef, type ChangeEvent, type DragEvent } from 'react';
import { UploadIcon } from './icons';
import type { DocumentGroup } from '../types/extraction';

interface SidebarProps {
  groups: DocumentGroup[];
  selectedGroupId: string | null;
  onSelect: (id: string) => void;
  onFiles: (files: File[]) => void;
}

export function Sidebar({ groups, selectedGroupId, onSelect, onFiles }: SidebarProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    onFiles(Array.from(event.target.files ?? []));
    event.target.value = '';
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    onFiles(Array.from(event.dataTransfer.files ?? []));
  };

  return (
    <div className="sidebar">
      <h2 className="sidebar-title">
        Document Queue <span className="text-muted">({groups.length})</span>
      </h2>

      <div
        className="upload-dropzone"
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
      >
        <UploadIcon />
        <span>Drag &amp; Drop or Click to Upload Files</span>
        <span>Select multiple files together to treat them as pages of one document</span>
      </div>
      <input ref={fileInputRef} type="file" multiple accept="image/*,application/pdf" className="hidden-file-input" onChange={handleFileChange} />

      <ul className="document-list">
        {groups.length === 0 ? (
          <li className="document-list-empty">No documents uploaded yet.</li>
        ) : (
          groups.map((group) => (
            <li
              key={group.id}
              className={`document-list-item ${group.id === selectedGroupId ? 'active' : ''}`}
              onClick={() => onSelect(group.id)}
            >
              <span className="file-name">{group.label}</span>
              <span className={`status-indicator ${group.status}`} />
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
