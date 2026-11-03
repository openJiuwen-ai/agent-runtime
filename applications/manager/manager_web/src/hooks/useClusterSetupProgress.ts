import { useAsync } from './useAsync';
import { useGuideStore } from '../stores/guideStore';
import { InstanceAgentResourceApi, InstanceApi, InstanceBindingApi, InstanceServiceResourceApi } from '../services/api';
import { collectPages } from '../utils/overview';
import { useRouter } from '../router';
import { deriveClusterSetupProgress, type ClusterSetupProgress } from '../utils/clusterSetupProgress';

/** Read saved configuration, including every resource page, to make every re-entry deterministic. */
export async function loadClusterSetupProgress(instanceId: string): Promise<ClusterSetupProgress> {
  const [instance, users, orgs, agents, runtimes] = await Promise.all([
    InstanceApi.get(instanceId),
    InstanceBindingApi.listUsers(instanceId),
    InstanceBindingApi.listOrgs(instanceId),
    collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size, enabled: true })),
    collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size, enabled: true })),
  ]);
  return deriveClusterSetupProgress({ instance, users: users.items, orgs: orgs.items, agents, runtimes });
}

export function useClusterSetupProgress(instanceId: string) {
  const revision = useGuideStore(state => state.revision);
  const { path } = useRouter();
  const state = useAsync(() => loadClusterSetupProgress(instanceId), [instanceId, revision, path]);
  // Never render another cluster's cached data while a new request is loading.
  return { ...state, data: state.data?.instance.jiuwenclaw_id === instanceId ? state.data : null };
}
