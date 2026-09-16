import { PIPELINE_STEPS } from '../utils/pipeline';

interface PipelineStepperProps {
  stage: number;
  failedStage?: string | null;
}

const FAILED_INDEX: Record<string, number> = {
  router: 0,
  extractor: 1,
  validator: 2,
  judge: 3,
};

export function PipelineStepper({ stage, failedStage }: PipelineStepperProps) {
  const failedIndex = failedStage && failedStage !== 'ocr' ? (FAILED_INDEX[failedStage] ?? -1) : -1;
  return (
    <div className="stepper">
      {failedStage === 'ocr' && (
        <div className="step failed ocr-failed" title="OCR is the responsible stage: transcription failed before routing">
          <div className="step-circle">!</div>
          <div className="step-label">OCR failed</div>
        </div>
      )}
      {PIPELINE_STEPS.map((step, index) => {
        const isFailed = index === failedIndex;
        const isCompleted = !isFailed && index < stage;
        const isActive = !isFailed && failedStage == null && index === stage;
        return (
          <div
            key={step}
            className={`step ${isCompleted ? 'completed' : ''} ${isActive ? 'active' : ''} ${isFailed ? 'failed' : ''}`}
            title={isFailed ? `${step} failed — responsible stage` : undefined}
          >
            <div className="step-circle">
              {isFailed ? (
                '!'
              ) : isCompleted ? (
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : (
                index + 1
              )}
            </div>
            <div className="step-label">{step}</div>
          </div>
        );
      })}
    </div>
  );
}
