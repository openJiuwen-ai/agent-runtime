import { clusterPagePath } from './clusterNavigation';

export type AdmissionTab = 'users' | 'orgs';

function localId(value: string | null | undefined): string | null {
  return value && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;
}

export function admissionPreparationCluster(params: URLSearchParams): string | null {
  return localId(params.get('admissionCluster'));
}

export function admissionTab(params: URLSearchParams): AdmissionTab {
  return params.get('accessTab') === 'users' ? 'users' : 'orgs';
}

/** Build only a local cluster route, preserving the pending setup and Agent handoff. */
export function clusterAccessPath(instanceId: string, tab: AdmissionTab, params = new URLSearchParams(), resume = false): string {
  const path = clusterPagePath(instanceId, 'access', params);
  const [pathname, search] = path.split('?');
  const query = new URLSearchParams(search);
  query.set('accessTab', tab);
  if (resume) query.set('admissionResume', '1');
  return `${pathname}?${query}`;
}

/** IAM preparation never accepts an arbitrary return URL or carries consumed modal actions. */
export function admissionCatalogPath(catalog: AdmissionTab, params = new URLSearchParams(), instanceId?: string): string {
  const cluster = localId(instanceId) || admissionPreparationCluster(params);
  if (!cluster) return `/${catalog}`;
  const access = clusterAccessPath(cluster, catalog, params);
  const query = new URLSearchParams(access.split('?')[1]);
  query.set('admissionCluster', cluster);
  if (params.get('admissionResume') === '1') query.set('admissionResume', '1');
  return `/${catalog}?${query}`;
}

export function admissionOrgPath(groupId: string, params = new URLSearchParams()): string {
  const search = admissionCatalogPath('orgs', params).split('?')[1];
  return `/orgs/${encodeURIComponent(groupId)}${search ? `?${search}` : ''}`;
}

export function admissionClusterReturnPath(params: URLSearchParams): string | null {
  const cluster = admissionPreparationCluster(params);
  return cluster ? clusterAccessPath(cluster, admissionTab(params), params, params.get('admissionResume') === '1') : null;
}

export interface AdmissionDraft {
  ids: string[];
  expiresAt: string;
  loginPolicy: 'allow' | 'deny';
}

export const emptyAdmissionDraft: AdmissionDraft = { ids: [], expiresAt: '', loginPolicy: 'allow' };

/** Only persist a cross-page handoff, isolated by signed-in user, cluster and subject kind. */
export function admissionDraftKey(userId: string, instanceId: string, tab: AdmissionTab): string {
  return `manager:admission-handoff:${JSON.stringify([userId, instanceId, tab])}`;
}

export function readAdmissionDraft(storage: Pick<Storage, 'getItem'>, key: string): AdmissionDraft {
  try {
    const value: unknown = JSON.parse(storage.getItem(key) || 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...emptyAdmissionDraft, ids: [] };
    const candidate = value as Partial<AdmissionDraft>;
    if (!Array.isArray(candidate.ids) || !candidate.ids.every(id => typeof id === 'string' && localId(id)) ||
      typeof candidate.expiresAt !== 'string' || !['allow', 'deny'].includes(candidate.loginPolicy ?? '')) {
      return { ...emptyAdmissionDraft, ids: [] };
    }
    return { ids: [...new Set(candidate.ids)], expiresAt: candidate.expiresAt, loginPolicy: candidate.loginPolicy as 'allow' | 'deny' };
  } catch { return { ...emptyAdmissionDraft, ids: [] }; }
}

export function writeAdmissionDraft(storage: Pick<Storage, 'setItem'>, key: string, draft: AdmissionDraft): void {
  try { storage.setItem(key, JSON.stringify(draft)); } catch { /* Storage restrictions do not prevent navigation. */ }
}

export function clearAdmissionDraft(storage: Pick<Storage, 'removeItem'>, key: string): void {
  try { storage.removeItem(key); } catch { /* Storage may be unavailable. */ }
}
