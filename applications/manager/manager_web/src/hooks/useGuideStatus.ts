import { useEffect, useState } from 'react';
import { useGuideStore } from '../stores/guideStore';
import {
  AgentTemplateApi, InstanceAgentResourceApi, InstanceBindingApi,
  InstanceServiceResourceApi, ServiceConfigTemplateApi,
} from '../services/api';

export function useDefinitionPresence() {
  const [agentDefined, setAgentDefined] = useState<boolean | null>(null);
  const [poolDefined, setPoolDefined] = useState<boolean | null>(null);
  const revision = useGuideStore((s) => s.revision);
  useEffect(() => {
    let cancelled = false;
    void AgentTemplateApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setAgentDefined((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setAgentDefined(null); });
    void ServiceConfigTemplateApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setPoolDefined((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setPoolDefined(null); });
    return () => { cancelled = true; };
  }, [revision]);
  return { agentDefined, poolDefined };
}

export function useClusterGuideStatus(instanceId: string | null | undefined) {
  const [hasAccessUser, setHasAccessUser] = useState<boolean | null>(null);
  const [hasAccessOrg, setHasAccessOrg] = useState<boolean | null>(null);
  const [hasAgentResource, setHasAgentResource] = useState<boolean | null>(null);
  const [hasPoolResource, setHasPoolResource] = useState<boolean | null>(null);
  const revision = useGuideStore((s) => s.revision);
  useEffect(() => {
    if (!instanceId) return;
    let cancelled = false;
    const mark = (set: (v: boolean | null) => void) => (r: { items?: unknown[] }) => {
      if (!cancelled) set((r.items?.length ?? 0) > 0);
    };
    const fail = (set: (v: boolean | null) => void) => () => {
      if (!cancelled) set(null);
    };
    void InstanceBindingApi.listUsers(instanceId).then(mark(setHasAccessUser)).catch(fail(setHasAccessUser));
    void InstanceBindingApi.listOrgs(instanceId).then(mark(setHasAccessOrg)).catch(fail(setHasAccessOrg));
    void InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page: 1, page_size: 1 }).then(mark(setHasAgentResource)).catch(fail(setHasAgentResource));
    void InstanceServiceResourceApi.listInstanceResources(instanceId, { page: 1, page_size: 1 }).then(mark(setHasPoolResource)).catch(fail(setHasPoolResource));
    return () => { cancelled = true; };
  }, [instanceId, revision]);
  return { hasAccessUser, hasAccessOrg, hasAgentResource, hasPoolResource };
}
