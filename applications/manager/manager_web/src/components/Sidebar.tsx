import { resourceCatalog } from '../config/resources';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useRouter } from '../router';
import { hasPermission, isPlatformAdmin, isWorkspaceQuotaEnabled } from '../services/api';

export function Sidebar() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const { path, navigate } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const label = (cn: string, en: string) => zh ? cn : en;
  const admin = isPlatformAdmin(user);
  const groups = [
    { name: label('工作台', 'Workspace'), items: [
      ['/overview', label('总览', 'Overview')],
      ['/instances', label('集群管理', 'Clusters')],
      ['/agent-templates', label('Agent模板管理', 'Agent templates')],
      ['/service-config-templates', t('serviceConfigTemplate.title')],
    ], show: admin },
    { name: label('资源中心', 'Resources'), items: resourceCatalog.map(resource => [resource.path, t(`${resource.key}.title`)]), show: admin },
    { name: label('运行环境', 'Environments'), items: [
      ['/container-templates', label('容器模板', 'Container templates')],
    ], show: admin },
    { name: label('访问授权', 'Access & authorization'), items: [
      ...(admin ? [['/users', label('用户', 'Users')], ['/orgs', label('组织', 'Organizations')]] : []),
      ...((admin || hasPermission(user, 'iam:role:read')) ? [['/roles', label('角色', 'Roles')]] : []),
    ], show: admin || hasPermission(user, 'iam:role:read') },
    { name: label('运营', 'Operations'), items: [['/approvals', label('审批中心', 'Approvals')]],
      show: isWorkspaceQuotaEnabled(user) && (admin || hasPermission(user, 'approval:read')) },
  ];
  return <aside className="nav manager-nav" aria-label={label('主导航', 'Main navigation')}>
    {groups.filter(g => g.show).map(group => <section key={group.name} className="manager-nav-group">
      <h2>{group.name}</h2>
      {group.items.map(([href, name]) => <button key={href} type="button"
        className={`nav-item ${path === href || path.startsWith(href + '/') || (href === '/overview' && path === '/') ? 'active' : ''}`}
        aria-current={path === href || path.startsWith(href + '/') ? 'page' : undefined}
        onClick={() => navigate(href)}><span className="nav-marker" aria-hidden="true" />{name}</button>)}
    </section>)}
    <div className="manager-nav-note">{label('JiuwenSwarm 管理工作台', 'JiuwenSwarm management')}</div>
  </aside>;
}
