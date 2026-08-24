import { useMemo, useState } from 'react';
import type { CombinedField } from './types/extraction';
import { Sidebar } from './components/Sidebar';
import { ExtractionTab } from './components/ExtractionTab';
import { EvaluationTab } from './components/EvaluationTab';
import { useDocumentQueue } from './hooks/useDocumentQueue';
import { useRecommendedModel } from './hooks/useRecommendedModel';

type TabId = 'extraction' | 'evaluation';

function App() {
  const modelName = useRecommendedModel();
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
    <>
      <Sidebar groups={groups} selectedGroupId={selectedGroupId} onSelect={selectGroup} onFiles={addFiles} />
      <div className="main-content">
        <div className="panel panel-pad">
          <h1 className="console-title">
            Extraction Console <span className="text-muted">({modelName})</span>
          </h1>

          <div className="tabs">
            <button className={currentTab === 'extraction' ? 'active' : ''} onClick={() => setCurrentTab('extraction')}>
              Extraction
            </button>
            <button className={currentTab === 'evaluation' ? 'active' : ''} onClick={() => setCurrentTab('evaluation')}>
              Evaluation
            </button>
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
      </div>
    </>
  );
}

export default App;
