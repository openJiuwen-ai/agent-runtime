export interface AgentBindingDraft { instance: string; name: string; expr: string }
export const emptyBindingDraft: AgentBindingDraft = { instance: '', name: '', expr: '' };
export function bindingDraftKey(userId: string, agentId: string, clusterId = ''): string {
  return `manager:binding-draft:${JSON.stringify(clusterId ? [userId, agentId, clusterId] : [userId, agentId])}`;
}
/** An explicit cluster handoff must never inherit another cluster's scope. */
export function bindingDraftForCluster(draft: AgentBindingDraft, clusterId: string): AgentBindingDraft {
  return clusterId && draft.instance !== clusterId ? { instance: clusterId, name: '', expr: '' } : draft;
}
export function readBindingDraft(storage: Pick<Storage, 'getItem'>, key: string): AgentBindingDraft {
  try {
    const value = JSON.parse(storage.getItem(key) || 'null');
    if (value && ['instance', 'name', 'expr'].every(field => typeof value[field] === 'string')) {
      return { instance: value.instance, name: value.name, expr: value.expr };
    }
  } catch { /* A damaged or unavailable draft must not block the form. */ }
  return { ...emptyBindingDraft };
}
export const emptyClusterBindingDraft = { addAgentId: '', addResourceName: '', addResourceDesc: '', addMatchExpr: '', addExpiresAt: '' };
export type ClusterBindingDraft = typeof emptyClusterBindingDraft;
export function clusterBindingDraftKey(userId: string, clusterId: string): string {
  return `manager:cluster-binding-handoff:${JSON.stringify([userId, clusterId])}`;
}
export function readClusterBindingHandoff(storage: Pick<Storage, 'getItem'>, key: string): ClusterBindingDraft | null {
  try {
    const value = JSON.parse(storage.getItem(key) || 'null');
    if (value && Object.keys(emptyClusterBindingDraft).every(field => typeof value[field] === 'string')) {
      return Object.fromEntries(Object.keys(emptyClusterBindingDraft).map(field => [field, value[field]])) as ClusterBindingDraft;
    }
  } catch { /* An unavailable browser draft must not block cluster configuration. */ }
  return null;
}
export function readClusterBindingDraft(storage: Pick<Storage, 'getItem'>, key: string): ClusterBindingDraft {
  return readClusterBindingHandoff(storage, key) || { ...emptyClusterBindingDraft };
}
/** Replace only the local draft; submitting a binding remains an explicit action. */
export function writeClusterBindingDraft(storage: Pick<Storage, 'setItem'>, key: string, draft: ClusterBindingDraft): boolean {
  try {
    storage.setItem(key, JSON.stringify(draft));
    return true;
  } catch { return false; }
}
export function bindingExpiry(expiresAt: string | null, now = Date.now()): 'unlimited' | 'expired' | 'scheduled' | 'unknown' {
  if (!expiresAt) return 'unlimited';
  const expiry = Date.parse(expiresAt);
  if (!Number.isFinite(expiry)) return 'unknown';
  return expiry <= now ? 'expired' : 'scheduled';
}

/** Explicit template choices preserve names and scopes already entered in the cluster. */
export function applyAgentTemplateChoice(
  draft: ClusterBindingDraft, templateId: string,
  catalog: { template_id: string; template_name: string; enabled: boolean }[],
): { draft: ClusterBindingDraft; error: 'missing' | 'disabled' | null } {
  const selected = catalog.find(item => item.template_id === templateId);
  if (!selected || selected.enabled === false) {
    return { draft: { ...draft, addAgentId: '' }, error: selected ? 'disabled' : 'missing' };
  }
  return {
    draft: { ...draft, addAgentId: selected.template_id, addResourceName: draft.addResourceName.trim() ? draft.addResourceName : selected.template_name.slice(0, 128) },
    error: null,
  };
}
