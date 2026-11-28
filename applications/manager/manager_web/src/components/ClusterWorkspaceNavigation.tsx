import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { useClusterGuideStatus } from '../hooks/useGuideStatus';
import { clusterNavigationGroup, clusterPagePath, type ClusterWorkspaceSection } from '../utils/clusterNavigation';
import { templateReturnPath } from '../utils/agentFlowContext';
import { ClusterManagementActions } from './ClusterManagementActions';
import { useGuideMissingLabel } from './GuideLink';
import { WarnBadge, type WarnBadgeLink } from './WarnBadge';

export type { ClusterWorkspaceSection } from '../utils/clusterNavigation';

/** One navigation model for every page belonging to a cluster, including setup flows. */
export function ClusterWorkspaceNavigation({ instanceId, name, section, onRefresh }: {
  instanceId: string;
  name: string;
  section: ClusterWorkspaceSection;
  onRefresh?: () => void;
}) {
  const { t } = useTranslation();
  const { navigate, params } = useRouter();
  const missingLabel = useGuideMissingLabel();
  const { hasAccessUser, hasAccessOrg, hasAgentResource, hasPoolResource } =
    useClusterGuideStatus(instanceId);
  const to = (target: ClusterWorkspaceSection) => {
    navigate(clusterPagePath(instanceId, target, params));
  };
  const pathFor = (target: ClusterWorkspaceSection) => clusterPagePath(instanceId, target, params);

  const accessWarnLinks: WarnBadgeLink[] | undefined =
    hasAccessUser === false && hasAccessOrg === false
      ? [
          {
            label: missingLabel(t('instanceDetail.accessPanel.instance.users')),
            to: pathFor('access'),
            openTarget: 'accessUsers',
          },
          {
            label: missingLabel(t('instanceDetail.accessPanel.instance.orgs')),
            to: pathFor('access'),
            openTarget: 'accessOrgs',
          },
        ]
      : undefined;
  const agentWarnLinks: WarnBadgeLink[] | undefined =
    hasAgentResource === false
      ? [{
          label: missingLabel(t('instanceDetail.resourcePanel.tabs.agent')),
          to: pathFor('agent-resources'),
          openTarget: 'agentResourceAdd',
        }]
      : undefined;
  const runtimeWarnLinks: WarnBadgeLink[] | undefined =
    hasPoolResource === false
      ? [{
          label: missingLabel(t('instanceDetail.resourcePanel.tabs.serviceResource')),
          to: pathFor('service-resources'),
          openTarget: 'serviceResourceAdd',
        }]
      : undefined;
  const workbenchWarnLinks: WarnBadgeLink[] = [
    ...(agentWarnLinks ?? []),
    ...(runtimeWarnLinks ?? []),
  ];

  const warnFor = (key: ClusterWorkspaceSection): WarnBadgeLink[] | undefined => {
    if (key === 'cluster-config' && workbenchWarnLinks.length > 0) return workbenchWarnLinks;
    if (key === 'access') return accessWarnLinks;
    if (key === 'agent-resources') return agentWarnLinks;
    if (key === 'service-resources') return runtimeWarnLinks;
    return undefined;
  };

  const main: { key: ClusterWorkspaceSection; label: string }[] = [
    { key: 'cluster-config', label: t('redesign.navWorkbench') },
    { key: 'access', label: t('redesign.navAccess') },
    { key: 'service-resources', label: t('redesign.navRuntime') },
    { key: 'agent-resources', label: t('redesign.navAgents') },
    { key: 'other-settings', label: t('redesign.navOther') },
    { key: 'status', label: t('redesign.navStatus') },
  ];
  const isSelected = (key: ClusterWorkspaceSection) => clusterNavigationGroup(section) === key;
  const isWorkbench = section === 'cluster-config';

  return <>
    <header className="cluster-workspace-header">
      <div className="cluster-workspace-header__identity">
        <button
          type="button"
          className="btn ghost sm shrink-0"
          onClick={() => navigate('/instances')}
          aria-label={t('redesign.returnToClusters')}
          title={t('redesign.returnToClusters')}
        >
          ←
        </button>
        <h1 className="page-title min-w-0 truncate" title={name}>{name}</h1>
      </div>
      <div className="cluster-workspace-header__actions">
        {params.has('returnTo') && (
          <button
            type="button"
            className="btn"
            onClick={() => navigate(templateReturnPath(params.get('returnTo')))}
          >
            {t('redesign.backToAgentTemplates')}
          </button>
        )}
        {isWorkbench && onRefresh && (
          <button type="button" className="btn" onClick={onRefresh}>{t('redesign.refreshConfig')}</button>
        )}
        {isWorkbench && <ClusterManagementActions instanceId={instanceId} name={name} onDeleted={() => navigate('/instances')} />}
        {!isWorkbench && (
          <button type="button" className="btn" onClick={() => to('cluster-config')}>{t('redesign.returnToWorkbench')}</button>
        )}
      </div>
      <p className="cluster-workspace-header__id text-xs text-muted mono break-all">{instanceId}</p>
    </header>
    <nav className="cluster-workspace-nav" aria-label={t('redesign.clusterPages')}>
      {main.map((item) => {
        const warnLinks = warnFor(item.key);
        return (
          <button
            key={item.key}
            type="button"
            className={`cluster-workspace-nav__item ${isSelected(item.key) ? 'active' : ''}`}
            aria-current={isSelected(item.key) ? 'page' : undefined}
            onClick={() => to(item.key)}
          >
            <span>{item.label}</span>
            {warnLinks && warnLinks.length > 0 ? <WarnBadge className="ml-1.5" links={warnLinks} /> : null}
          </button>
        );
      })}
    </nav>
  </>;
}
