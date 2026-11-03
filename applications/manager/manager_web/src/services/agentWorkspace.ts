import {
  A2AAccessPolicyTemplateApi, EmbeddingTemplateApi, ExtensionTemplateApi,
  InstanceAgentResourceApi, InstanceApi, McpTemplateApi, ModelTemplateApi,
  PermissionsTemplateApi, SkillPrebuiltTemplateApi, type AgentTemplate,
} from './api';
import { collectPages } from '../utils/overview';
import { parseRefChain } from '../utils/templateRef';

const catalogs: Record<string, { get: (id: string) => Promise<{ template_name: string; enabled: boolean }>; path: string }> = {
  ...Object.fromEntries(['default_model', 'video_model', 'audio_model', 'vision_model', 'image_gen_model'].map(slot => [slot, { get: id => ModelTemplateApi.get(id), path: '/model-templates' }])),
  embedding_model: { get: id => EmbeddingTemplateApi.get(id), path: '/embedding-templates' },
  skill_prebuilt: { get: id => SkillPrebuiltTemplateApi.get(id), path: '/skill-prebuilt-templates' },
  extension_config: { get: id => ExtensionTemplateApi.get(id), path: '/extension-config-templates' },
  mcp: { get: id => McpTemplateApi.get(id), path: '/mcp-templates' },
  permissions: { get: id => PermissionsTemplateApi.get(id), path: '/safety-guardrails' },
  a2a_access_policy: { get: async id => { const policy = await A2AAccessPolicyTemplateApi.get(id); return { template_name: policy.policy_name, enabled: policy.enabled }; }, path: '/a2a-management?tab=policies' },
};

export async function loadAgentReferences(agent: AgentTemplate) {
  const rows = [];
  // Sequential reads avoid flooding the API with large imported definitions.
  for (const [slot, refs] of Object.entries(agent.template_ref || {})) {
    for (const ref of refs) {
      for (const segment of parseRefChain(ref).segments) {
        if (segment.mode !== 'template') {
          rows.push({ slot, id: ref, name: ref, enabled: true, path: undefined, error: null, dynamic: true });
          continue;
        }
        const id = segment.templateId;
        if (!id) continue;
        const catalog = catalogs[slot];
        try {
          if (!catalog) throw new Error('Unsupported reference type');
          const resource = await catalog.get(id);
          rows.push({ slot, id, name: resource.template_name, enabled: resource.enabled, path: catalog.path, error: null, dynamic: false });
        } catch (error) {
          rows.push({ slot, id, name: id, enabled: false, path: catalog?.path, error: (error as Error).message, dynamic: false });
        }
      }
    }
  }
  return rows;
}

export async function loadAgentBindings(templateId: string) {
  const instances = await collectPages((page, page_size) => InstanceApi.list({ page, page_size }));
  const results = [];
  for (const instance of instances) {
    try {
      const resources = await collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instance.jiuwenclaw_id, { page, page_size }));
      results.push({ instance, resources: resources.filter(r => r.ref_template_id === templateId), error: null });
    } catch (error) {
      results.push({ instance, resources: [], error: (error as Error).message });
    }
  }
  return results;
}
