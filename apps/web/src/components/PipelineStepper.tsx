import { PIPELINE_STEPS } from '../utils/pipeline';

interface PipelineStepperProps {
  stage: number;
}

export function PipelineStepper({ stage }: PipelineStepperProps) {
  return (
    <div className="stepper">
      {PIPELINE_STEPS.map((step, index) => (
        <div key={step} className={`step ${index < stage ? 'completed' : ''} ${index === stage ? 'active' : ''}`}>
          <div className="step-circle">{index + 1}</div>
          <div className="step-label">{step}</div>
        </div>
      ))}
    </div>
  );
}
