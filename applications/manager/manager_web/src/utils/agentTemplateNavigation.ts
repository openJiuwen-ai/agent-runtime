/** Single catalog URL, including compatibility for old bookmarks and query context. */
export const AGENT_TEMPLATE_PATH = '/agent-templates';
export function canonicalAgentPath(path: string): string | null {
  return path === '/agents' || path.startsWith('/agents/')
    ? path.replace(/^\/agents(?=\/|$)/, AGENT_TEMPLATE_PATH) : null;
}
export const agentReferenceSlots: Record<string, string[]> = {
  model: ['default_model', 'video_model', 'audio_model', 'vision_model', 'image_gen_model'],
  embedding: ['embedding_model'], skill: ['skill_prebuilt'], mcp: ['mcp'],
  extension: ['extension_config'], permission: ['permissions'], 'a2a-policy': ['a2a_access_policy'],
  'a2a-outbound': ['a2a_access_policy'], 'expert-package': ['expert_package'],
};

export function agentResourceReferencesPath(type: string, id: string, name: string): string {
  return `${AGENT_TEMPLATE_PATH}?${new URLSearchParams({ refType: type, refId: id, refName: name })}`;
}

/** Return to the source catalog, retaining the relevant A2A tab. */
export function agentResourceCatalogPath(type: string | null | undefined): string | null {
  const paths: Record<string, string> = {
    model: '/model-templates',
    embedding: '/embedding-templates',
    skill: '/skill-prebuilt-templates',
    mcp: '/mcp-templates',
    extension: '/extension-config-templates',
    permission: '/safety-guardrails',
    'a2a-policy': '/a2a-management?tab=policies',
    'a2a-outbound': '/a2a-management?tab=agents',
    'expert-package': '/expert-package-templates',
  };
  return type && Object.prototype.hasOwnProperty.call(paths, type) ? paths[type] : null;
}
