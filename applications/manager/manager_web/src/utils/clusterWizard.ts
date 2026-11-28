import { templateReturnPath } from './agentFlowContext';
import type { ClusterSetupSection } from './clusterSetupProgress';

export type ClusterWizardStep = 0 | 1 | 2 | 3;
export type GuidedClusterStage = 1 | 2 | 3 | 4 | 5;

export function guidedClusterStage(value: string | null): GuidedClusterStage | null {
  return value !== null && /^[1-5]$/.test(value) ? Number(value) as GuidedClusterStage : null;
}

/** Resume at the first incomplete saved configuration within the five-step flow. */
export function guidedClusterResumeStage(section: ClusterSetupSection): GuidedClusterStage {
  return section === 'access' ? 2 : section === 'service-resources' ? 3 : section === 'agent-resources' ? 4 : 5;
}

/** Explicit stage distinguishes the guided flow from legacy four-step bookmarks. */
export function guidedClusterWizardPath(instance: string, stage: GuidedClusterStage, context = new URLSearchParams()): string {
  const query = new URLSearchParams({ instance, stage: String(stage) });
  const template = context.get('template') || context.get('templateId');
  if (template) query.set('template', template);
  if (context.has('returnTo')) query.set('returnTo', templateReturnPath(context.get('returnTo')));
  return '/configuration-wizard?' + query;
}
export type ClusterSetupTarget = 'cluster-config' | 'access' | 'service-resources' | 'agent-resources' | 'status';

export function clusterWizardStep(value: string | null, hasInstance: boolean): ClusterWizardStep {
  if (!hasInstance) return 0;
  return value !== null && /^[0-3]$/.test(value) ? Number(value) as ClusterWizardStep : 1;
}

/** Direct editing keeps existing cluster pages and preserves the setup context. */
export function clusterSetupPath(instance: string, section: ClusterSetupTarget, context = new URLSearchParams()): string {
  const query = new URLSearchParams({ setup: '1' });
  const template = context.get('template') || context.get('templateId');
  if (template) query.set('template', template);
  if (context.has('returnTo')) query.set('returnTo', templateReturnPath(context.get('returnTo')));
  return '/instances/' + encodeURIComponent(instance) + '/' + section + '?' + query;
}

/** Compatibility for bookmarks created by the former four-page wizard. */
export function clusterWizardPath(instance: string, step: ClusterWizardStep, template?: string | null, returnTo?: string | null): string {
  const section: ClusterSetupTarget = step === 0 || step === 3 ? 'status' : step === 1 ? 'access' : 'service-resources';
  const context = new URLSearchParams();
  if (template) context.set('template', template);
  if (returnTo) context.set('returnTo', returnTo);
  return clusterSetupPath(instance, section, context);
}

export function legacyClusterWizardPath(params: URLSearchParams): string | null {
  const instance = params.get('instance');
  if (!instance) return null;
  if (guidedClusterStage(params.get('stage'))) return null;
  if (!params.has('step')) return clusterSetupPath(instance, 'cluster-config', params);
  return clusterWizardPath(instance, clusterWizardStep(params.get('step'), true), params.get('template') || params.get('templateId'), params.get('returnTo'));
}

/** Carry workflow context only within this cluster; normalize old wizardStep links. */
export function carryClusterWizard(target: string, instance: string, params: URLSearchParams): string {
  if (!target.startsWith('/instances/' + encodeURIComponent(instance) + '/')) return target;
  const [path, search = ''] = target.split('?');
  const query = new URLSearchParams(search);
  if (params.get('setup') === '1' || params.has('wizardStep')) query.set('setup', '1');
  const template = params.get('template') || params.get('templateId');
  if (template && !query.has('template')) query.set('template', template);
  if (params.has('returnTo') && !query.has('returnTo')) query.set('returnTo', templateReturnPath(params.get('returnTo')));
  query.delete('wizardStep');
  return path + (query.size ? '?' + query : '');
}
