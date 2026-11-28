import { WizardResourceBinding, type WizardResourceProps } from './WizardResourceBinding';

export function WizardAgentStep(props: WizardResourceProps) {
  return <WizardResourceBinding {...props} kind="agent" />;
}
