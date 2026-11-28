export type ClusterCreationHostMode = 'auto' | 'manual';
export const clusterCreationEndpointKeys = ['gateway', 'runtime', 'web', 'gatewayHttp', 'gatewayWs'] as const;
export type ClusterCreationEndpointKey = typeof clusterCreationEndpointKeys[number];
export type ClusterCreationHosts = Record<ClusterCreationEndpointKey, string>;
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
  name: '', description: '', namespace: '', hostMode: 'auto',
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
    if (draft.hostMode === 'auto' && draft.namespace === 'default' && !draft.name && !draft.description && clusterCreationEndpointKeys.every(key => !draft[key])) return { ...emptyClusterCreationDraft };
    return draft;
  } catch { return { ...emptyClusterCreationDraft }; }
}
export function saveClusterCreationDraft(storage: Pick<Storage, 'setItem'>, key: string, draft: ClusterCreationDraft): void {
  try { storage.setItem(key, JSON.stringify(draft)); } catch { /* Browser storage may be unavailable. */ }
}
export function clearClusterCreationDraft(storage: Pick<Storage, 'removeItem'>, key: string): void {
  try { storage.removeItem(key); } catch { /* Browser storage may be unavailable. */ }
}
export function clusterCreationHosts(namespace: string): ClusterCreationHosts {
  if (!namespace.trim()) return { gateway: '', runtime: '', web: '', gatewayHttp: '', gatewayWs: '' };
  const suffix = '.' + namespace.trim();
  return {
    gateway: 'http://jiuwenswarm-gateway' + suffix + ':19000',
    runtime: 'http://jiuwenswarm-agent-runtime' + suffix + ':8091',
    web: 'http://jiuwenswarm-web' + suffix + ':5173',
    gatewayHttp: 'http://jiuwenswarm-gateway' + suffix + ':19000',
    gatewayWs: 'http://jiuwenswarm-gateway' + suffix + ':19000',
  };
}

/** Legacy automatic drafts ignored their hidden manual values. Preserve that behavior on migration. */
export function resolveClusterCreationHosts(draft: ClusterCreationDraft): ClusterCreationHosts {
  if (draft.hostMode === 'auto') return clusterCreationHosts(draft.namespace);
  return {
    gateway: draft.gateway, runtime: draft.runtime, web: draft.web,
    gatewayHttp: draft.gatewayHttp, gatewayWs: draft.gatewayWs,
  };
}

/** The unified form always presents the actual endpoints as editable values. */
export function normalizeClusterCreationDraft(draft: ClusterCreationDraft): ClusterCreationDraft {
  return { ...draft, ...resolveClusterCreationHosts(draft), hostMode: 'manual' };
}

/** Standard Gateway user endpoints follow the management URL until customized independently. */
export function updateClusterCreationEndpoint(draft: ClusterCreationDraft, key: ClusterCreationEndpointKey, value: string): ClusterCreationDraft {
  const current = normalizeClusterCreationDraft(draft);
  const next = { ...current, [key]: value };
  if (key === 'gateway') {
    for (const related of ['gatewayHttp', 'gatewayWs'] as const) {
      if (current[related] === current.gateway) next[related] = value;
    }
  }
  return next;
}

/** Follow namespace changes for suggested endpoints while keeping explicitly customized addresses. */
export function updateClusterCreationNamespace(draft: ClusterCreationDraft, namespace: string): ClusterCreationDraft {
  const current = normalizeClusterCreationDraft(draft);
  const previousDefaults = clusterCreationHosts(current.namespace);
  const nextDefaults = clusterCreationHosts(namespace);
  const next = { ...current, namespace };
  for (const key of clusterCreationEndpointKeys) {
    if (current[key] === previousDefaults[key]) next[key] = nextDefaults[key];
  }
  return next;
}
