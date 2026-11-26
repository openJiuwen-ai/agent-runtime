import { useMemo } from 'react';
import { collectPages, allComponentsConnected } from '../utils/overview';
import { useTranslation } from 'react-i18next';
import { InstanceApi, SystemApi } from '../services/api';
import { useAsync } from '../hooks/useAsync';
import { useClustersGuideStatus, useDefinitionPresence } from '../hooks/useGuideStatus';
import { useRouter } from '../router';
import { StatusBadge } from '../components/StatusBadge';
import { WarnBadge, type WarnBadgeLink } from '../components/WarnBadge';
import { WorkspaceNote } from '../components/WorkspaceNote';

export const loadOverviewInstances = () => collectPages((page, page_size) => InstanceApi.list({ page, page_size }));

export function OverviewPage() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  const { navigate } = useRouter();
  const instances = useAsync(loadOverviewInstances, []);
  const health = useAsync(() => SystemApi.health(), []);
  const { agentDefined, agentDefinitionDefined, poolDefined, instanceCreated, modelDefined } = useDefinitionPresence();
  const rows = instances.data || [];
  const healthy = rows.filter(allComponentsConnected);
  const attention = rows.filter(r => !healthy.includes(r));

  const clusterIds = useMemo(
    () => rows.map((it) => it.jiuwenclaw_id),
    [rows],
  );
  const alertsByInstance = useClustersGuideStatus(clusterIds);
  const hasClusterAlerts = Object.values(alertsByInstance).some((alerts) => alerts.length > 0);

  /** 与左侧导航栏叹号同源：模型 → Agent模板 → 运行时模板 → Agent ID → 集群 → 集群告警 */
  const quickNavLinks: WarnBadgeLink[] = [];
  if (modelDefined === false) {
    quickNavLinks.push({ label: t('guide.missingItem', { item: t('nav.modelTemplates') }), to: '/model-templates', openTarget: 'modelTemplateNew' });
  }
  if (agentDefined === false) {
    quickNavLinks.push({ label: t('guide.missingItem', { item: t('nav.agentManagement') }), to: '/agent-templates', openTarget: 'agentTemplateNew' });
  }
  if (poolDefined === false) {
    quickNavLinks.push({ label: t('guide.missingItem', { item: t('nav.serviceConfigTemplates') }), to: '/service-config-templates/new' });
  }
  if (agentDefinitionDefined === false) {
    quickNavLinks.push({ label: t('guide.missingItem', { item: t('nav.agentDefinitions') }), to: '/agent-definitions', openTarget: 'agentDefinitionNew' });
  }
  if (instanceCreated === false) {
    quickNavLinks.push({ label: t('guide.missingItem', { item: t('nav.instances') }), to: '/instances', openTarget: 'instanceCreate' });
  }
  if (hasClusterAlerts) {
    quickNavLinks.push({ label: t('guide.clusterAlerts'), to: '/instances' });
  }

  return <div className="workspace-overview">
    <div className="page-header"><div><h1 className="page-title">{tr('管理总览', 'Management overview')}</h1><p className="page-subtitle">{tr('从服务连接到 Agent 使用，掌握配置与运行环境。', 'Manage service connectivity, Agent configuration and environments.')}</p></div>
      <button className="btn" disabled={instances.loading || health.loading} onClick={() => { void instances.reload(); void health.reload(); }}>{tr('刷新状态', 'Refresh')}</button></div>
    <div className="overview-metrics">
      <div className="card"><span className="text-muted">{tr('管理 API', 'Management API')}</span><strong>{health.loading ? '…' : health.error ? tr('连接失败', 'Unavailable') : health.data?.status === 'ok' ? tr('连接正常', 'Connected') : tr('状态未知', 'Unknown')}</strong><small>{health.error || tr('表示管理服务是否可访问', 'Indicates whether the management service is accessible')}</small></div>
      <div className="card"><span className="text-muted">{tr('服务实例', 'Service instances')}</span><strong>{instances.data ? rows.length : '—'}</strong><small>{tr('已接入管理的服务环境', 'Registered service environments')}</small></div>
      <div className="card"><span className="text-muted">{tr('组件连接正常', 'Components connected')}</span><strong>{instances.data ? healthy.length : '—'}</strong><small>Gateway · Runtime</small></div>
      <div className="card"><span className="text-muted">{tr('待检查实例', 'Instances to inspect')}</span><strong>{instances.data ? attention.length : '—'}</strong><small>{tr('存在离线或未知组件', 'Offline or unknown components')}</small></div>
    </div>
    <div className="card overview-checklist">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-muted">{t('overview.quickNav')}</span>
        {quickNavLinks.length > 0 ? (
          <WarnBadge className="!h-5 !w-5 !text-[11px] opacity-100" links={quickNavLinks} />
        ) : (
          <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-ok text-ok text-[10px] font-bold leading-none" title={t('overview.noAlerts')}>✓</span>
        )}
        <strong className="!text-base">{t('overview.unconfiguredItems')}{quickNavLinks.length > 0 ? ` (${quickNavLinks.length})` : ''}</strong>
      </div>
      <p className="mt-2 text-xs text-muted m-0">
        {quickNavLinks.length > 0 ? quickNavLinks.map((l) => l.label).join(zh ? '、' : ', ') : t('overview.noAlerts')}
      </p>
    </div>
    {instances.error && <div className="card text-danger" role="alert">{tr('无法读取实例：', 'Unable to load instances: ')}{instances.error}</div>}
    <section className="card overview-environments"><div className="card-header"><h2>{tr('服务环境', 'Service environments')}</h2><button className="btn" onClick={() => navigate('/instances')}>{tr('管理集群实例', 'Manage cluster instances')} →</button></div>
      {instances.loading ? <p role="status" className="overview-empty">{tr('正在读取服务状态…', 'Loading service status…')}</p> : instances.data && !rows.length ? <div className="overview-empty"><h3>{tr('还没有接入服务实例', 'No service instances connected')}</h3><p>{tr('先准备能力资源，再接入环境并配置 Agent 使用范围。', 'Prepare resources, connect an environment and configure Agent access.')}</p><button className="btn primary" onClick={() => navigate('/configuration-wizard?from=cluster')}>{tr('新建集群', 'Create a cluster')} →</button></div> : instances.data && <div className="table-wrap"><table className="table"><thead><tr><th>{tr('名称', 'Name')}</th><th>Gateway</th><th>Runtime</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody>{[...attention, ...healthy].slice(0, 8).map(r => <tr key={r.jiuwenclaw_id}><td><b>{r.jiuwenclaw_name}</b><div className="text-muted text-xs">{r.namespace || '—'}</div></td><td><StatusBadge status={r.gateway_status} /></td><td><StatusBadge status={r.runtime_status} /></td><td><button className="btn ghost sm" onClick={() => navigate(`/instances/${r.jiuwenclaw_id}/status`)}>{tr('检查', 'Inspect')} →</button></td></tr>)}</tbody></table></div>}
    </section>
    <WorkspaceNote title={tr('从资源准备到集群配置', 'From resource preparation to cluster setup')}>
      {tr('Agent 资源准备可复用定义，Agent 负责组合能力，集群管理负责将准入、运行资源和 Agent 绑定到实际环境。', 'Agent resources prepare reusable definitions, Agents compose capabilities, and cluster management binds admission, runtime and Agents to a real environment.')}
    </WorkspaceNote>
  </div>;
}
