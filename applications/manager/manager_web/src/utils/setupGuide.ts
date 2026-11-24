import { clusterPagePath, type ClusterWorkspaceSection } from './clusterNavigation';

export interface SetupGuideSelection { stage: number; clusterId: string; showOptional: boolean }
export const emptySetupGuide: SetupGuideSelection = { stage: 0, clusterId: '', showOptional: false };

export function readSetupGuide(raw: string | null): SetupGuideSelection {
  try {
    const saved = JSON.parse(raw || 'null');
    if (saved?.version !== 2) return { ...emptySetupGuide };
    return {
      stage: Number.isInteger(saved.stage) && saved.stage >= 0 && saved.stage < 4 ? saved.stage : 0,
      clusterId: typeof saved.clusterId === 'string' && /^[\w-]{1,128}$/.test(saved.clusterId) ? saved.clusterId : '',
      showOptional: saved.showOptional === true,
    };
  } catch { return { ...emptySetupGuide }; }
}

export function setupGuideRouteCluster(path: string, params: URLSearchParams): string {
  const match = path.match(/^\/instances\/([^/]+)(?:\/[^/]+)?$/);
  try {
    const id = match ? decodeURIComponent(match[1]) : params.get('cluster') || params.get('admissionCluster') || '';
    return /^[\w-]{1,128}$/.test(id) ? id : '';
  } catch { return ''; }
}

export function setupGuideClusterPath(id: string, section: ClusterWorkspaceSection, path: string, params: URLSearchParams): string | null {
  if (!id) return null;
  const routeCluster = setupGuideRouteCluster(path, params);
  return clusterPagePath(id, section, routeCluster === id ? params : new URLSearchParams());
}
