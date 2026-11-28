import type { AgentDefinition, InstanceAgentResource, InstanceServiceResource } from '../services/api';
import { isActiveConfiguration } from './clusterSetupProgress';
import { matchExprToEditorString } from './matchExpr';

/** An explicit template handoff starts a new binding when that template is not bound yet. */
export function initialWizardResource<T extends InstanceServiceResource | InstanceAgentResource>(resources: T[], templateId?: string): T | undefined {
  if (templateId) return resources.find(row => row.ref_template_id === templateId);
  return resources.find(row => isActiveConfiguration(row)) ?? resources.find(row => row.enabled) ?? resources[0];
}

export function definitionForPair(definitions: AgentDefinition[], configId: string, runtimeId: string): AgentDefinition | undefined {
  return definitions.find(item => item.agent_config_template_id === configId && item.runtime_template_id === runtimeId);
}

export async function ensureWizardAgentDefinition(
  api: { list: () => Promise<AgentDefinition[]>; create: () => Promise<AgentDefinition> },
  configId: string, runtimeId: string,
): Promise<AgentDefinition> {
  const existing = definitionForPair(await api.list(), configId, runtimeId);
  if (existing) return existing;
  try { return await api.create(); }
  catch (error) {
    const recovered = definitionForPair(await api.list(), configId, runtimeId);
    if (recovered) return recovered;
    throw error;
  }
}

/** Disabled / expired resources cannot supply a runtime to an Agent definition. */
export function activeRuntimeTemplateIds(resources: InstanceServiceResource[], enabledTemplateIds: string[]): string[] {
  const enabled = new Set(enabledTemplateIds);
  return [...new Set(resources.filter(row => isActiveConfiguration(row) && enabled.has(row.ref_template_id)).map(row => row.ref_template_id))];
}

export function runtimePriorityConflict(resources: InstanceServiceResource[], priority: number, editingId?: string): boolean {
  return resources.some(row => row.resource_id !== editingId && row.enabled && row.priority === priority);
}

/** On retry after a partially successful request, reuse only this attempt's exact binding. */
export function matchingAttemptResource<T extends InstanceServiceResource | InstanceAgentResource>(
  resources: T[], attempt: { templateId: string; name: string; scope: string; priority?: number; enabled: boolean; expiresAt: string | null },
): T | undefined {
  return resources.find(row => row.ref_template_id === attempt.templateId && row.resource_name === attempt.name
    && matchExprToEditorString(row.match_expr) === attempt.scope
    && row.enabled === attempt.enabled && ((!row.expires_at && !attempt.expiresAt)
      || Boolean(row.expires_at && attempt.expiresAt && Date.parse(row.expires_at) === Date.parse(attempt.expiresAt)))
    && (attempt.priority === undefined || ('priority' in row && row.priority === attempt.priority)));
}
