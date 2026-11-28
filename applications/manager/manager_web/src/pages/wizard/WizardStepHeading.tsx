import type { ReactNode } from 'react';
import './WizardStepHeading.css';

export function WizardStepHeading({ step, title, description, actions }: { step: string; title: string; description?: string; actions?: ReactNode }) {
  return <header className="wizard-step-heading">
    <div>
      <span className="wizard-step-heading__step">{step}</span>
      <h2>{title}</h2>
      {description && <p>{description}</p>}
    </div>
    {actions && <div className="wizard-step-heading__actions">{actions}</div>}
  </header>;
}
