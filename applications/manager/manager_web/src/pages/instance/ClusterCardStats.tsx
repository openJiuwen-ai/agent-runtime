import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { InstanceAgentResourceApi, InstanceBindingApi, InstanceServiceResourceApi } from '../../services/api';
import { activeAdmissionRules, isActiveConfiguration } from '../../utils/clusterSetupProgress';
import { collectPages } from '../../utils/overview';

/** Counts saved active bindings, independently of component connectivity. */
export function ClusterCardStats({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const stats = useAsync(async () => {
    const [agents, users, orgs, runtimes] = await Promise.all([
      collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size, enabled: true })),
      InstanceBindingApi.listUsers(instanceId),
      InstanceBindingApi.listOrgs(instanceId),
      collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size, enabled: true })),
    ]);
    const now = Date.now();
    return {
      agents: agents.filter((row) => isActiveConfiguration(row, now)).length,
      admission: activeAdmissionRules(users.items, now).length + activeAdmissionRules(orgs.items, now).length,
      runtimes: runtimes.filter((row) => isActiveConfiguration(row, now)).length,
    };
  }, [instanceId]);
  const items = [
    { key: 'agents' as const, label: t('redesign.cardStats.agents'), hint: t('redesign.cardStats.agentsHint') },
    { key: 'admission' as const, label: t('redesign.cardStats.admission'), hint: t('redesign.cardStats.admissionHint') },
    { key: 'runtimes' as const, label: t('redesign.cardStats.runtimes'), hint: t('redesign.cardStats.runtimesHint') },
  ];

  return (
    <div>
      <div className="cluster-card__stats" aria-busy={stats.loading}>
        {items.map((item) => (
          <div key={item.key} title={item.hint}>
            <span>{item.label}</span>
            <strong>{stats.loading ? '…' : stats.data?.[item.key] ?? '—'}</strong>
          </div>
        ))}
      </div>
      {stats.error && (
        <div className="flex items-center justify-between gap-2 mt-2 text-xs">
          <span className="text-danger" role="alert" title={stats.error}>{t('redesign.cardStats.loadFailed')}</span>
          <button type="button" className="btn sm" onClick={() => void stats.reload()}>{t('redesign.cardStats.retry')}</button>
        </div>
      )}
    </div>
  );
}
