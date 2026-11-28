import { WizardResourceBinding, type WizardResourceProps } from './WizardResourceBinding';

export function WizardRuntimeStep(props: WizardResourceProps) {
  return <WizardResourceBinding {...props} kind="runtime" />;
}
