export type ClusterCreationHostMode = 'auto' | 'manual';
export interface ClusterCreationDraft {
  name: string;
  description: string;
  namespace: string;
  hostMode: ClusterCreationHostMode;
  gateway: string;
  runtime: string;
  web: string;
  gatewayHttp: string;
  gatewayWs: string;
}
export const emptyClusterCreationDraft: ClusterCreationDraft = {
  name: '', description: '', namespace: 'default', hostMode: 'auto',
  gateway: '', runtime: '', web: '', gatewayHttp: '', gatewayWs: '',
};
export function clusterCreationDraftKey(userId: string, template = '', returnTo = ''): string {
  return 'manager:cluster-creation-draft:' + JSON.stringify([userId, template, returnTo]);
}
/** Restore known fields only; an unavailable or corrupt draft never blocks creation. */
export function readClusterCreationDraft(storage: Pick<Storage, 'getItem'>, key: string): ClusterCreationDraft {
  try {
    const value = JSON.parse(storage.getItem(key) || 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...emptyClusterCreationDraft };
    const draft = { ...emptyClusterCreationDraft };
    for (const key of Object.keys(draft) as (keyof ClusterCreationDraft)[]) {
      if (key === 'hostMode') {
        if (value[key] === 'auto' || value[key] === 'manual') draft[key] = value[key];
      } else if (typeof value[key] === 'string') draft[key] = value[key];
    }
    return draft;
  } catch { return { ...emptyClusterCreationDraft }; }
}
export function saveClusterCreationDraft(storage: Pick<Storage, 'setItem'>, key: string, draft: ClusterCreationDraft): void {
  try { storage.setItem(key, JSON.stringify(draft)); } catch { /* Browser storage may be unavailable. */ }
}
export function clearClusterCreationDraft(storage: Pick<Storage, 'removeItem'>, key: string): void {
  try { storage.removeItem(key); } catch { /* Browser storage may be unavailable. */ }
}
export function clusterCreationHosts(namespace: string) {
  const suffix = '.' + (namespace.trim() || 'default');
  return {
    gateway: 'http://jiuwenswarm-gateway' + suffix + ':19000',
    runtime: 'http://jiuwenswarm-agent-runtime' + suffix + ':8091',
    web: 'http://jiuwenswarm-web' + suffix + ':5173',
    gatewayHttp: 'http://jiuwenswarm-gateway' + suffix + ':19000',
    gatewayWs: 'http://jiuwenswarm-gateway' + suffix + ':19000',
  };
}
