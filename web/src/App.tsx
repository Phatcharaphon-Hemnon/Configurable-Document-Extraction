import { useMemo, useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { ExtractionTab } from './components/ExtractionTab';
import { EvaluationTab } from './components/EvaluationTab';
import { MoonIcon, SunIcon } from './components/icons';
import { useDocumentQueue } from './hooks/useDocumentQueue';
import { useRecommendedModel } from './hooks/useRecommendedModel';
import { useTheme } from './hooks/useTheme';
import type { CombinedField } from './types/extraction';

type TabId = 'extraction' | 'evaluation';

function App() {
  const modelName = useRecommendedModel();
  const { theme, toggle } = useTheme();
  const {
    groups,
    selectedGroupId,
    selectedGroup,
    selectedDocIndex,
    selectedDoc,
    selectDocIndex,
    selectGroup,
    addFiles,
  } = useDocumentQueue();
  const [currentTab, setCurrentTab] = useState<TabId>('extraction');

  const combinedFields = useMemo<CombinedField[]>(
    () => (selectedDoc ? selectedDoc.fields.map((f) => [f.name, f]) : []),
    [selectedDoc],
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <FileTextIconInline />
          </span>
          <span className="brand-name">DocExtract</span>
        </div>
        <span className="model-chip" title="Extraction model">{modelName}</span>
        <button
          className="theme-toggle"
          onClick={toggle}
          aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          title={theme === 'light' ? 'Dark mode' : 'Light mode'}
        >
          {theme === 'light' ? <MoonIcon /> : <SunIcon />}
        </button>
      </header>

      <div className="app-body">
        <Sidebar groups={groups} selectedGroupId={selectedGroupId} onSelect={selectGroup} onFiles={addFiles} />

        <main className="main-content">
          <div className="panel">
            <div className="panel-head">
              <h1 className="console-title">Extraction Console</h1>
              <div className="tabs">
                <button className={currentTab === 'extraction' ? 'active' : ''} onClick={() => setCurrentTab('extraction')}>
                  Extraction
                </button>
                <button className={currentTab === 'evaluation' ? 'active' : ''} onClick={() => setCurrentTab('evaluation')}>
                  Evaluation
                </button>
              </div>
            </div>

            {currentTab === 'extraction' ? (
              <ExtractionTab
                group={selectedGroup}
                doc={selectedDoc}
                docIndex={selectedDocIndex}
                onSelectDoc={selectDocIndex}
                combinedFields={combinedFields}
              />
            ) : (
              <EvaluationTab
                groupId={selectedGroupId}
                docIndex={selectedDocIndex}
                doc={selectedDoc}
                combinedFields={combinedFields}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function FileTextIconInline() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

export default App;
