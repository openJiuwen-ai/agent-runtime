import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { InstanceAgentResourceApi, InstanceBindingApi, InstanceServiceResourceApi } from '../../services/api';
import { activeAdmissionRules, isActiveConfiguration } from '../../utils/clusterSetupProgress';
import { collectPages } from '../../utils/overview';

/** Counts saved active bindings, independently of component connectivity. */
export function ClusterCardStats({ instanceId }: { instanceId: string }) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const stats = useAsync(async () => {
    const [agents, users, orgs, runtimes] = await Promise.all([
      collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size, enabled: true })),
      InstanceBindingApi.listUsers(instanceId),
      InstanceBindingApi.listOrgs(instanceId),
      collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size, enabled: true })),
    ]);
    const now = Date.now();
    return {
      agents: agents.filter(row => isActiveConfiguration(row, now)).length,
      admission: activeAdmissionRules(users.items, now).length + activeAdmissionRules(orgs.items, now).length,
      runtimes: runtimes.filter(row => isActiveConfiguration(row, now)).length,
    };
  }, [instanceId]);
  const items = [
    { key: 'agents' as const, label: 'Agent', hint: tr('已启用且未过期的 Agent 绑定数量。', 'Enabled, unexpired Agent bindings.') },
    { key: 'admission' as const, label: tr('准入', 'Admission'), hint: tr('允许准入的有效用户与组织授权数量，不包含组织成员人数。', 'Active user and organization admission grants, excluding organization member counts.') },
    { key: 'runtimes' as const, label: tr('运行池', 'Runtime pools'), hint: tr('已启用且未过期的运行池绑定数量，不代表正在运行的容器数。', 'Enabled, unexpired runtime pool bindings, not running containers.') },
  ];

  return <div>
    <div className="cluster-card__stats" aria-busy={stats.loading}>
      {items.map(item => <div key={item.key} title={item.hint}>
        <span>{item.label}</span>
        <strong>{stats.loading ? '…' : stats.data?.[item.key] ?? '—'}</strong>
      </div>)}
    </div>
    {stats.error && <div className="flex items-center justify-between gap-2 mt-2 text-xs">
      <span className="text-danger" role="alert" title={stats.error}>{tr('配置数量加载失败', 'Failed to load configuration counts')}</span>
      <button type="button" className="btn sm" onClick={() => void stats.reload()}>{tr('重试', 'Retry')}</button>
    </div>}
  </div>;
}
