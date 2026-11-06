import { templateReturnPath } from './agentFlowContext';

export const clusterSetupSections = ['access', 'service-resources', 'agent-resources', 'status'] as const;
export type ClusterWorkspaceSection = 'cluster-config' | typeof clusterSetupSections[number] | 'other-settings' | 'config' | 'workspace-quota' | 'team-quota' | 'token-quota' | 'cost' | 'audit';
export type ClusterNavigationGroup = 'cluster-config' | typeof clusterSetupSections[number] | 'other-settings';
export const clusterSettingSections = ['log-masking', 'logging', 'audit-log', 'cron-quota', 'workspace-quota', 'team-quota'] as const;
export type ClusterSettingSection = typeof clusterSettingSections[number];
const legacySettingSections: Record<string, ClusterSettingSection> = {
  'workspace-quota': 'workspace-quota', 'team-quota': 'team-quota',
  'token-quota': 'log-masking', cost: 'log-masking', audit: 'log-masking',
};

export function clusterNavigationGroup(section: ClusterWorkspaceSection): ClusterNavigationGroup {
  if (section === 'config' || Object.prototype.hasOwnProperty.call(legacySettingSections, section)) return 'other-settings';
  return section as ClusterNavigationGroup;
}

/** Copy only this flow's context, never page-specific filters or consumed modal actions. */
export function clusterPagePath(instanceId: string, section: ClusterWorkspaceSection | 'setup', context = new URLSearchParams()): string {
  const query = new URLSearchParams();
  const template = context.get('template') || context.get('templateId');
  if (template) query.set('template', template);
  if (context.has('returnTo')) query.set('returnTo', templateReturnPath(context.get('returnTo')));
  if (context.get('setup') === '1') query.set('setup', '1');
  return `/instances/${encodeURIComponent(instanceId)}/${section}${query.size ? `?${query}` : ''}`;
}

export function clusterQuotaSection(workspaceEnabled: boolean): ClusterWorkspaceSection {
  return workspaceEnabled ? 'workspace-quota' : 'team-quota';
}

export function canonicalClusterSection(section: string | undefined, _workspaceEnabled = false): ClusterWorkspaceSection | 'setup' | null {
  if (!section) return 'cluster-config';
  if (section === 'resources') return 'agent-resources';
  if (section === 'config' || section === 'quota' || Object.prototype.hasOwnProperty.call(legacySettingSections, section)) return 'other-settings';
  const allowed = ['cluster-config', ...clusterSetupSections, 'other-settings', 'setup'];
  return allowed.includes(section) ? section as ClusterWorkspaceSection | 'setup' : null;
}

/** Legacy bookmarks land on the corresponding visible section of the settings page. */
export function canonicalClusterPath(instanceId: string, section: string | undefined, context = new URLSearchParams(), workspaceEnabled = false): string {
  if (section === 'expert-packages') {
    const query = new URLSearchParams({ cluster: instanceId, returnTo: clusterPagePath(instanceId, 'agent-resources', context) });
    return `/expert-package-templates?${query}`;
  }
  const canonical = canonicalClusterSection(section, workspaceEnabled) || 'cluster-config';
  const [pathname, search = ''] = clusterPagePath(instanceId, canonical, context).split('?');
  const query = new URLSearchParams(search);
  if (canonical === 'other-settings') {
    const configTabs: Record<string, ClusterSettingSection> = { logMasking: 'log-masking', logging: 'logging', auditLog: 'audit-log', cronPolicy: 'cron-quota' };
    const requested = context.get('setting');
    const setting = section === 'config' ? configTabs[context.get('configTab') || ''] || 'log-masking'
      : section === 'quota' ? clusterQuotaSection(workspaceEnabled)
      : legacySettingSections[section || ''] || (clusterSettingSections.includes(requested as ClusterSettingSection) ? requested : null);
    if (setting) query.set('setting', setting);
  }
  return `${pathname}${query.size ? `?${query}` : ''}`;
}
