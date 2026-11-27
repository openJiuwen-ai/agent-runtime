import { AgentTemplateApi, InstanceAgentResourceApi } from './api';
import { collectPages } from '../utils/overview';

export interface MatchAgentOption { id: string; label: string }

/** Runtime rules match cluster Agent resource IDs, not reusable configuration IDs. */
export async function loadMatchAgentOptions(instanceId?: string): Promise<MatchAgentOption[]> {
  if (instanceId !== undefined) {
    if (!instanceId) return [];
    // Rules may be prepared before activation or renewal; execution checks availability separately.
    const resources = await collectPages((page, page_size) =>
      InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size }),
    );
    return resources.map((resource) => ({
      id: resource.resource_id,
      label: resource.resource_name || resource.resource_id,
    }));
  }

  // Preserve the existing template-based choices for other editor consumers.
  const templates = await collectPages((page, page_size) => AgentTemplateApi.list({ page, page_size }));
  return templates.map((template) => ({ id: template.template_id, label: template.template_name || template.template_id }));
}
