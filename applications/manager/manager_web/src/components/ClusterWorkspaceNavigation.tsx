import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import type { ClusterSetupProgress } from '../utils/clusterSetupProgress';
import { clusterNavigationGroup, clusterPagePath, type ClusterWorkspaceSection } from '../utils/clusterNavigation';
import { templateReturnPath } from '../utils/agentFlowContext';

export type { ClusterWorkspaceSection } from '../utils/clusterNavigation';

/** One navigation model for every page belonging to a cluster, including the demo. */
export function ClusterWorkspaceNavigation({ instanceId, name, section, progress, onRefresh }: {
  instanceId: string;
  name: string;
  section: ClusterWorkspaceSection;
  progress?: ClusterSetupProgress | null;
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
      <div className="min-w-0">
        <button className="btn ghost sm" onClick={() => navigate('/instances')}>← {tr('集群管理', 'Clusters')}</button>
        <div className="mt-2 flex min-w-0 items-center gap-3"><h1 className="page-title truncate" title={name}>{name}</h1>{instanceId === 'mock-cluster-demo' && <span className="tag shrink-0">{tr('演示集群', 'Demo')}</span>}</div>
        <p className="mt-1 text-xs text-muted mono break-all">{instanceId}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {params.has('returnTo') && <button className="btn ghost sm" onClick={() => navigate(templateReturnPath(params.get('returnTo')))}>{tr('返回 Agent 模板', 'Return to Agent templates')}</button>}
        {isWorkbench && onRefresh && <button className="btn sm" onClick={onRefresh}>{tr('刷新进度', 'Refresh progress')}</button>}
        {isWorkbench && progress && <button className="btn primary sm" onClick={() => to(progress.nextSection)}>{progress.configurationComplete ? tr('检查并验证', 'Check and verify') : tr('继续配置', 'Continue setup')} →</button>}
      </div>
    </header>
    <nav className="cluster-workspace-nav" aria-label={tr('集群页面', 'Cluster pages')}>
      {main.map(item => <button key={item.key} type="button" className={`cluster-workspace-nav__item ${isSelected(item.key) ? 'active' : ''}`} aria-current={isSelected(item.key) ? 'page' : undefined} onClick={() => to(item.key)}>{item.label}</button>)}
    </nav>
  </>;
}

export function ClusterWorkspaceReturnAction({ instanceId }: { instanceId: string }) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const target = (key: ClusterWorkspaceSection) => {
    navigate(clusterPagePath(instanceId, key, params));
  };
  return <div className="cluster-page-actions">
    <button className="btn" onClick={() => target('cluster-config')}>{zh ? '返回配置工作台' : 'Configuration workspace'}</button>
  </div>;
}
