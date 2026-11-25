import { resourceCatalog } from '../config/resources';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useRouter } from '../router';
import { hasPermission, isPlatformAdmin, isApprovalEnabled } from '../services/api';

type NavItem = [href: string, name: string];

type NavGroup = {
  name?: string;
  items: NavItem[];
  show: boolean;
};

export function Sidebar() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const { path, navigate } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const label = (cn: string, en: string) => (zh ? cn : en);
  const admin = isPlatformAdmin(user);
  const identityItems: NavItem[] = [];
  if (admin) {
    identityItems.push(['/users', t('nav.users')], ['/orgs', t('nav.orgs')]);
  }
  if (admin || hasPermission(user, 'iam:role:read')) {
    identityItems.push(['/roles', t('nav.roles')]);
  }
  const groups: NavGroup[] = [
    {
      items: [
        ['/overview', t('nav.overview')],
        ['/instances', t('nav.instances')],
        ['/deployments', t('nav.deployments')],
      ],
      show: admin,
    },
    {
      name: t('nav.agentResources'),
      items: [
        ['/agent-templates', t('nav.agentManagement')],
        ['/agent-definitions', t('nav.agentDefinitions')],
        ...resourceCatalog.map((resource): NavItem => [resource.path, t(`${resource.key}.title`)]),
      ],
      show: admin,
    },
    {
      name: t('nav.runtimeResources'),
      items: [
        ['/service-config-templates', t('nav.serviceConfigTemplates')],
        ['/container-templates', t('nav.containerTemplates')],
      ],
      show: admin,
    },
    {
      name: t('nav.iam'),
      items: identityItems,
      show: identityItems.length > 0,
    },
    {
      name: t('nav.operations'),
      items: [['/approvals', t('nav.approvals')]],
      show: isApprovalEnabled(user) && (admin || hasPermission(user, 'approval:read')),
    },
  ];
  return (
    <aside className="nav manager-nav" aria-label={label('主导航', 'Main navigation')}>
      {groups
        .filter((g) => g.show && g.items.length > 0)
        .map((group, index) => (
          <section key={group.name ?? `nav-group-${index}`} className="manager-nav-group">
            {group.name ? <h2>{group.name}</h2> : null}
            {group.items.map(([href, name]) => (
              <button
                key={href}
                type="button"
                className={`nav-item ${path === href || path.startsWith(`${href}/`) || (href === '/overview' && path === '/') ? 'active' : ''}`}
                aria-current={path === href || path.startsWith(`${href}/`) ? 'page' : undefined}
                onClick={() => navigate(href)}
              >
                <span className="nav-marker" aria-hidden="true" />
                {name}
              </button>
            ))}
          </section>
        ))}
      <div className="manager-nav-note">{label('JiuwenSwarm 管理工作台', 'JiuwenSwarm management')}</div>
    </aside>
  );
}
