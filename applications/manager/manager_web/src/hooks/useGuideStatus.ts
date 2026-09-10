import { useEffect, useState } from 'react';
import { useGuideStore } from '../stores/guideStore';
import {
  AgentTemplateApi, InstanceAgentResourceApi, InstanceApi, InstanceBindingApi,
  InstanceServiceResourceApi, ModelTemplateApi, ServiceConfigTemplateApi,
} from '../services/api';

export function useDefinitionPresence() {
  const [agentDefined, setAgentDefined] = useState<boolean | null>(null);
  const [poolDefined, setPoolDefined] = useState<boolean | null>(null);
  const [instanceCreated, setInstanceCreated] = useState<boolean | null>(null);
  const [modelDefined, setModelDefined] = useState<boolean | null>(null);
  const revision = useGuideStore((s) => s.revision);
  useEffect(() => {
    let cancelled = false;
    void AgentTemplateApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setAgentDefined((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setAgentDefined(null); });
    void ServiceConfigTemplateApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setPoolDefined((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setPoolDefined(null); });
    void InstanceApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setInstanceCreated((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setInstanceCreated(null); });
    void ModelTemplateApi.list({ page: 1, page_size: 1 })
      .then((r) => { if (!cancelled) setModelDefined((r.items?.length ?? 0) > 0); })
      .catch(() => { if (!cancelled) setModelDefined(null); });
    return () => { cancelled = true; };
  }, [revision]);
  return { agentDefined, poolDefined, instanceCreated, modelDefined };
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

/** 某集群当前的引导告警项（未配置准入用户/组织、Agent、Agent实例池），供总览/列表页汇总展示。 */
export type ClusterGuideAlert = {
  labelKey: 'accessUsers' | 'accessOrgs' | 'agentResource' | 'poolResource';
  to: string;
  openTarget: 'accessUsers' | 'accessOrgs' | 'agentResourceAdd' | 'serviceResourceAdd';
};

/** 批量查询多个集群的引导状态：返回 clusterId → 告警项数组（无告警为空数组）。 */
export function useClustersGuideStatus(instanceIds: string[]) {
  const [alertsByInstance, setAlertsByInstance] = useState<Record<string, ClusterGuideAlert[]>>({});
  const revision = useGuideStore((s) => s.revision);
  const idsKey = instanceIds.join(',');
  useEffect(() => {
    const ids = idsKey ? idsKey.split(',') : [];
    if (!ids.length) {
      setAlertsByInstance({});
      return;
    }
    let cancelled = false;
    const pending: Promise<void>[] = [];
    const next: Record<string, ClusterGuideAlert[]> = {};
    for (const id of ids) {
      next[id] = [];
      const nonEmpty = (set: (empty: boolean) => void) => (r: { items?: unknown[] }) => set((r.items?.length ?? 0) > 0);
      const skip = () => {};
      const collect = (id: string, key: ClusterGuideAlert['labelKey'], to: string, openTarget: ClusterGuideAlert['openTarget'], empty: boolean) => {
        if (!cancelled && empty) {
          next[id] = [...(next[id] ?? []), { labelKey: key, to, openTarget }];
        }
      };
      pending.push(
        InstanceBindingApi.listUsers(id).then(nonEmpty((empty) => collect(id, 'accessUsers', `/instances/${id}/access`, 'accessUsers', empty))).catch(skip),
        InstanceBindingApi.listOrgs(id).then(nonEmpty((empty) => collect(id, 'accessOrgs', `/instances/${id}/access`, 'accessOrgs', empty))).catch(skip),
        InstanceAgentResourceApi.listInstanceAgentResources(id, { page: 1, page_size: 1 }).then(nonEmpty((empty) => collect(id, 'agentResource', `/instances/${id}/agent-resources`, 'agentResourceAdd', empty))).catch(skip),
        InstanceServiceResourceApi.listInstanceResources(id, { page: 1, page_size: 1 }).then(nonEmpty((empty) => collect(id, 'poolResource', `/instances/${id}/service-resources`, 'serviceResourceAdd', empty))).catch(skip),
      );
    }
    void Promise.all(pending).then(() => {
      if (!cancelled) setAlertsByInstance(next);
    });
    return () => { cancelled = true; };
  }, [idsKey, revision]);
  return alertsByInstance;
}
