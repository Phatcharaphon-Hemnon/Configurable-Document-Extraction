import { PIPELINE_STEPS } from '../utils/pipeline';

interface PipelineStepperProps {
  stage: number;
}

export function PipelineStepper({ stage }: PipelineStepperProps) {
  return (
    <div className="stepper">
      {PIPELINE_STEPS.map((step, index) => (
        <div key={step} className={`step ${index < stage ? 'completed' : ''} ${index === stage ? 'active' : ''}`}>
          <div className="step-circle">
            {index < stage ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              index + 1
            )}
          </div>
          <div className="step-label">{step}</div>
        </div>
      ))}
    </div>
  );
}
