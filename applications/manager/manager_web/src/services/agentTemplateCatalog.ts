import { AgentTemplateApi, A2AAccessPolicyTemplateApi, type AgentTemplate } from './api';
import { collectPages } from '../utils/overview';
import { parseRefChain } from '../utils/templateRef';
import { agentReferenceSlots } from '../utils/agentTemplateNavigation';

export function referencesResource(agent: AgentTemplate, type: string, id: string): boolean {
  if (!Object.prototype.hasOwnProperty.call(agentReferenceSlots, type)) return false;
  return (agentReferenceSlots[type] || []).some(slot =>
    (agent.template_ref?.[slot] || []).some(ref => parseRefChain(ref).segments.some(segment =>
      segment.mode === 'template' && segment.templateId === id)));
}

export async function loadAgentTemplateCatalog(
  query: NonNullable<Parameters<typeof AgentTemplateApi.list>[0]>,
  refType?: string | null, refId?: string | null,
) {
  if (!refType || !refId) return AgentTemplateApi.list(query);
  // The API has no resource-reference filter; filter all server-matched pages before paginating.
  const all = await collectPages((page, page_size) => AgentTemplateApi.list({ ...query, page, page_size }));
  // Outbound A2A references are indirect: a policy's allowlist/denylist determines coverage.
  // Match the backend's reference-count semantics, including disabled definitions/policies.
  const coveredPolicies = refType === 'a2a-outbound'
    ? (await collectPages((page, page_size) => A2AAccessPolicyTemplateApi.list({ page, page_size })))
      .filter(policy => policy.mode === 'denylist'
        ? !policy.member_template_ids.includes(refId)
        : policy.member_template_ids.includes(refId))
    : null;
  const matches = all.filter(agent => coveredPolicies
    ? coveredPolicies.some(policy => referencesResource(agent, 'a2a-policy', policy.policy_id))
    : referencesResource(agent, refType, refId));
  const page = query.page || 1;
  const page_size = query.page_size || 20;
  return { items: matches.slice((page - 1) * page_size, page * page_size), total: matches.length, page, page_size };
}
