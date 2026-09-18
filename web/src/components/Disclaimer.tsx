import type { ReactNode } from 'react';

type DisclaimerProps = {
  /** Predefined disclaimer presets, or a free-form children body. */
  variant?: 'extraction' | 'evaluation' | 'history';
  children?: ReactNode;
};

const PRESETS: Record<NonNullable<DisclaimerProps['variant']>, string> = {
  extraction:
    'AI-extracted values are model estimates (confidence is not measured accuracy). ' +
    'Fields marked needs review require human verification before use.',
  evaluation:
    'Evaluation scores are computed against the supplied ground truth only. ' +
    'A high score does not certify the extracted data — review flagged fields.',
  history:
    'Stored results were produced by the AI pipeline at the time of extraction. ' +
    'Always re-verify accepted data before acting on it.',
};

/**
 * Formal, reusable AI-output disclaimer. Rendered on every tab that shows
 * pipeline output (extraction results, history, evaluation) so users always
 * see the review obligation next to the data.
 */
export function Disclaimer({ variant, children }: DisclaimerProps) {
  const text = variant ? PRESETS[variant] : null;
  return (
    <div className="disclaimer" role="note">
      <span className="disclaimer-tag">AI output</span>
      <span className="disclaimer-text">
        {text}
        {children}
      </span>
    </div>
  );
}
