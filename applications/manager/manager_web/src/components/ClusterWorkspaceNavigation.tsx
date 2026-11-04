import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { clusterNavigationGroup, clusterPagePath, type ClusterWorkspaceSection } from '../utils/clusterNavigation';
import { templateReturnPath } from '../utils/agentFlowContext';
import { ClusterManagementActions } from './ClusterManagementActions';

export type { ClusterWorkspaceSection } from '../utils/clusterNavigation';

/** One navigation model for every page belonging to a cluster, including setup flows. */
export function ClusterWorkspaceNavigation({ instanceId, name, section, onRefresh }: {
  instanceId: string;
  name: string;
  section: ClusterWorkspaceSection;
  onRefresh?: () => void;
}) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  const to = (target: ClusterWorkspaceSection) => {
    navigate(clusterPagePath(instanceId, target, params));
  };
  const main: { key: ClusterWorkspaceSection; label: string }[] = [
    { key: 'cluster-config', label: tr('配置工作台', 'Configuration workspace') },
    { key: 'access', label: tr('集群准入', 'Admission') },
    { key: 'service-resources', label: tr('运行资源', 'Runtime resources') },
    { key: 'agent-resources', label: tr('Agent绑定', 'Agent bindings') },
    { key: 'other-settings', label: tr('其他配置', 'Other settings') },
    { key: 'status', label: tr('连接状态', 'Connection status') },
  ];
  const isSelected = (key: ClusterWorkspaceSection) => clusterNavigationGroup(section) === key;
  const isWorkbench = section === 'cluster-config';

  return <>
    <header className="cluster-workspace-header">
      <div className="cluster-workspace-header__identity">
        <h1 className="page-title min-w-0 truncate" title={name}>{name}</h1>
        <button type="button" className="btn" onClick={() => navigate('/instances')}>← {tr('集群管理', 'Clusters')}</button>
      </div>
      <div className="cluster-workspace-header__actions">
        {params.has('returnTo') && <button type="button" className="btn" onClick={() => navigate(templateReturnPath(params.get('returnTo')))}>{tr('返回 Agent 模板', 'Return to Agent templates')}</button>}
        {isWorkbench && onRefresh && <button type="button" className="btn" onClick={onRefresh}>{tr('刷新配置', 'Refresh configuration')}</button>}
        {isWorkbench && <ClusterManagementActions instanceId={instanceId} name={name} onDeleted={() => navigate('/instances')} />}
        {!isWorkbench && <button type="button" className="btn" onClick={() => to('cluster-config')}>{tr('返回配置工作台', 'Configuration workspace')}</button>}
      </div>
      <p className="cluster-workspace-header__id text-xs text-muted mono break-all">{instanceId}</p>
    </header>
    <nav className="cluster-workspace-nav" aria-label={tr('集群页面', 'Cluster pages')}>
      {main.map(item => <button key={item.key} type="button" className={`cluster-workspace-nav__item ${isSelected(item.key) ? 'active' : ''}`} aria-current={isSelected(item.key) ? 'page' : undefined} onClick={() => to(item.key)}>{item.label}</button>)}
    </nav>
  </>;
}
