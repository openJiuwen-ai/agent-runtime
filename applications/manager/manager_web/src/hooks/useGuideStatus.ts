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

/** 某集群当前的引导告警项（未配置准入用户/组织、Agent、运行时），供总览/列表页汇总展示。 */
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
    const accessResults: Record<string, [boolean | null, boolean | null]> = {};
    const nonEmpty = (r: { items?: unknown[] }) => (r.items?.length ?? 0) > 0;
    const skip = () => {};
    for (const id of ids) {
      next[id] = [];
      accessResults[id] = [null, null];
      const collect = (key: ClusterGuideAlert['labelKey'], to: string, openTarget: ClusterGuideAlert['openTarget'], empty: boolean) => {
        if (!cancelled && empty) {
          next[id] = [...(next[id] ?? []), { labelKey: key, to, openTarget }];
        }
      };
      // 准入与详情页口径一致：用户和组织均未配置才提醒，两项结果汇总后再判定
      pending.push(
        InstanceBindingApi.listUsers(id)
          .then((r) => { accessResults[id][0] = nonEmpty(r); })
          .catch(() => { accessResults[id][0] = null; }),
        InstanceBindingApi.listOrgs(id)
          .then((r) => { accessResults[id][1] = nonEmpty(r); })
          .catch(() => { accessResults[id][1] = null; }),
        InstanceAgentResourceApi.listInstanceAgentResources(id, { page: 1, page_size: 1 })
          .then((r) => collect('agentResource', `/instances/${id}/agent-resources`, 'agentResourceAdd', !nonEmpty(r)))
          .catch(skip),
        InstanceServiceResourceApi.listInstanceResources(id, { page: 1, page_size: 1 })
          .then((r) => collect('poolResource', `/instances/${id}/service-resources`, 'serviceResourceAdd', !nonEmpty(r)))
          .catch(skip),
      );
    }
    void Promise.all(pending).then(() => {
      if (cancelled) return;
      for (const id of ids) {
        const [hasUsers, hasOrgs] = accessResults[id] ?? [null, null];
        if (hasUsers === false && hasOrgs === false) {
          next[id] = [
            { labelKey: 'accessUsers', to: `/instances/${id}/access`, openTarget: 'accessUsers' },
            { labelKey: 'accessOrgs', to: `/instances/${id}/access`, openTarget: 'accessOrgs' },
            ...(next[id] ?? []),
          ];
        }
      }
      setAlertsByInstance(next);
    });
    return () => { cancelled = true; };
  }, [idsKey, revision]);
  return alertsByInstance;
}
