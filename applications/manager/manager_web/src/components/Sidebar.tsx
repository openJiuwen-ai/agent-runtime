import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { useAsync } from '../hooks/useAsync';
import { useDefinitionPresence } from '../hooks/useGuideStatus';
import { WarnBadge, type WarnBadgeLink } from './WarnBadge';
import { useGuideMissingLabel } from './GuideLink';
import { useAuth } from '../auth/AuthContext';
import { hasPermission, InstanceApi, isApprovalEnabled, isPlatformAdmin } from '../services/api';

type NavItem = {
  key: string;
  pathPrefix: string;
  href: string;
  label: string;
  icon: ReactNode;
};

type NavGroup = {
  key: string;
  label: string;
  icon: ReactNode;
  items: NavItem[];
  show: boolean;
};

function isItemActive(item: NavItem, path: string): boolean {
  if (item.pathPrefix === '/overview') {
    return path === '/' || path === '/overview' || path.startsWith('/overview/');
  }
  return path === item.pathPrefix || path.startsWith(`${item.pathPrefix}/`);
}

function pathActive(prefixes: string[], path: string): boolean {
  return prefixes.some((p) => path === p || path.startsWith(`${p}/`));
}

function icon(paths: string) {
  return (
    <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d={paths} />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`w-3.5 h-3.5 nav-subgroup__chevron ${open ? 'open' : ''}`}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      strokeWidth={2}
      aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
    </svg>
  );
}

export function Sidebar() {
  const { t, i18n } = useTranslation();
  const { path, navigate } = useRouter();
  const { user } = useAuth();
  const zh = i18n.language.startsWith('zh');
  const missingLabel = useGuideMissingLabel();
  const admin = isPlatformAdmin(user);

  const { data: instancesPage } = useAsync(
    () =>
      admin
        ? InstanceApi.list({ page: 1, page_size: 50 })
        : Promise.resolve({ items: [], total: 0, page: 1, page_size: 50 }),
    [admin],
  );
  const { agentDefined, agentDefinitionDefined, poolDefined, instanceCreated, modelDefined } =
    useDefinitionPresence(admin);

  const topItems: NavItem[] = [
    {
      key: 'overview',
      pathPrefix: '/overview',
      href: '/overview',
      label: t('nav.overview'),
      icon: icon('M3 13h2v8H3v-8zm6-6h2v14H9V7zm6 3h2v11h-2V10zm6-6h2v17h-2V4z'),
    },
    {
      key: 'instances',
      pathPrefix: '/instances',
      href: '/instances',
      label: t('nav.instances'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <circle cx="6" cy="6" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="12" cy="18" r="2" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 8v3a2 2 0 002 2h8a2 2 0 002-2V8M12 13v3" />
        </svg>
      ),
    },
    {
      key: 'deployments',
      pathPrefix: '/deployments',
      href: '/deployments',
      label: t('nav.deployments'),
      icon: icon(
        'M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 006.16-12.12A14.98 14.98 0 009.63 8.41m5.96 5.96a14.93 14.93 0 01-5.84 2.58m-.12-8.54a6 6 0 00-7.38 5.84h4.8m2.58-5.84a14.93 14.93 0 00-2.58 5.84m2.7 2.7c-.1.02-.21.04-.31.06a15.09 15.09 0 01-2.45-2.45c.02-.1.04-.21.06-.31m-2.24 2.39a4.49 4.49 0 00-1.76 4.3 4.49 4.49 0 004.31-1.75M16.5 9a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z',
      ),
    },
  ];

  const agentResourceItems: NavItem[] = [
    {
      key: 'agent-templates',
      pathPrefix: '/agent-templates',
      href: '/agent-templates',
      label: t('nav.agentTemplates'),
      icon: icon('M9 3h6m-3 0v3m-5 0h10a2 2 0 012 2v9a2 2 0 01-2 2H7a2 2 0 01-2-2V8a2 2 0 012-2zM9 13h.01M15 13h.01'),
    },
    {
      key: 'model-templates',
      pathPrefix: '/model-templates',
      href: '/model-templates',
      label: t('nav.modelTemplates'),
      icon: icon('M4 7l8-4 8 4-8 4-8-4zm0 6l8 4 8-4M4 19l8 4 8-4'),
    },
    {
      key: 'skill-prebuilt-templates',
      pathPrefix: '/skill-prebuilt-templates',
      href: '/skill-prebuilt-templates',
      label: t('nav.skillWhitelistTemplates'),
      icon: icon(
        'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
      ),
    },
    {
      key: 'mcp-templates',
      pathPrefix: '/mcp-templates',
      href: '/mcp-templates',
      label: t('nav.mcpTemplates'),
      icon: icon('M8 9l3 3-3 3m5 0h3M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z'),
    },
    {
      key: 'safety-guardrails',
      pathPrefix: '/safety-guardrails',
      href: '/safety-guardrails',
      label: t('nav.safetyGuardrails'),
      icon: icon('M12 3l8 4v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V7l8-4z'),
    },
    {
      key: 'embedding-templates',
      pathPrefix: '/embedding-templates',
      href: '/embedding-templates',
      label: t('nav.embeddingTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v16h16" />
          <circle cx="9.5" cy="9.5" r="1.4" />
          <circle cx="15.5" cy="12.5" r="1.4" />
          <circle cx="10.5" cy="15.5" r="1.4" />
        </svg>
      ),
    },
    {
      key: 'a2a-management',
      pathPrefix: '/a2a-management',
      href: '/a2a-management',
      label: t('nav.a2aManagement'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <circle cx="6" cy="12" r="2" />
          <circle cx="18" cy="6" r="2" />
          <circle cx="18" cy="18" r="2" />
          <path d="M8 11l8-4M8 13l8 4" />
        </svg>
      ),
    },
    {
      key: 'expert-package-templates',
      pathPrefix: '/expert-package-templates',
      href: '/expert-package-templates',
      label: t('nav.expertPackages'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 7h16M4 12h16M4 17h10" />
          <circle cx="18" cy="17" r="2" />
        </svg>
      ),
    },
    {
      key: 'extension-templates',
      pathPrefix: '/extension-config-templates',
      href: '/extension-config-templates',
      label: t('nav.extensionTemplates'),
      icon: icon('M14 4l6 6m0 0l-6 6m6-6H4M4 20h7'),
    },
  ];

  const runtimeResourceItems: NavItem[] = [
    {
      key: 'service-config-templates',
      pathPrefix: '/service-config-templates',
      href: '/service-config-templates',
      label: t('nav.runtimeTemplates'),
      icon: icon(
        'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01',
      ),
    },
    {
      key: 'container-templates',
      pathPrefix: '/container-templates',
      href: '/container-templates',
      label: t('nav.containerTemplates'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <rect x="3.5" y="6.5" width="17" height="11" rx="1" />
          <path strokeLinecap="round" d="M8 7v10M12 7v10M16 7v10" />
        </svg>
      ),
    },
  ];

  const identityItems: NavItem[] = [
    ...(admin
      ? [
          {
            key: 'users',
            pathPrefix: '/users',
            href: '/users',
            label: t('nav.users'),
            icon: icon('M15 19.5a3 3 0 00-6 0M12 11a3 3 0 100-6 3 3 0 000 6zM3 19.5a9 9 0 0118 0'),
          },
          {
            key: 'orgs',
            pathPrefix: '/orgs',
            href: '/orgs',
            label: t('nav.orgs'),
            icon: icon(
              'M3 21h18M5 21V7l7-4 7 4v14M9 21v-4h6v4M9 10h.01M15 10h.01M9 13h.01M15 13h.01',
            ),
          },
        ]
      : []),
    ...(admin || hasPermission(user, 'iam:role:read')
      ? [
          {
            key: 'roles',
            pathPrefix: '/roles',
            href: '/roles',
            label: t('nav.roles'),
            icon: icon('M12 3l7 4v5c0 4.5-2.9 7.8-7 9-4.1-1.2-7-4.5-7-9V7l7-4zm-3 9l2 2 4-4'),
          },
        ]
      : []),
  ];

  const groups: NavGroup[] = [
    {
      key: 'agent-resources',
      label: t('nav.agentResources'),
      icon: icon('M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z'),
      items: agentResourceItems,
      show: admin,
    },
    {
      key: 'runtime-resources',
      label: t('nav.runtimeResources'),
      icon: icon('M13 10V3L4 14h7v7l9-11h-7z'),
      items: runtimeResourceItems,
      show: admin,
    },
    {
      key: 'iam',
      label: t('nav.iam'),
      icon: icon('M12 3l7 4v5c0 4.5-2.9 7.8-7 9-4.1-1.2-7-4.5-7-9V7l7-4z'),
      items: identityItems,
      show: identityItems.length > 0,
    },
    {
      key: 'operations',
      label: t('nav.operations'),
      icon: icon('M9 5h6m-7 4h8m-8 4h5M7 3h10a2 2 0 012 2v14l-4-2-4 2-4-2-4 2V5a2 2 0 012-2z'),
      items: [
        {
          key: 'approvals',
          pathPrefix: '/approvals',
          href: '/approvals',
          label: t('nav.approvals'),
          icon: icon('M9 12l2 2 4-4M7 3h10a2 2 0 012 2v14l-4-2-4 2-4-2-4 2V5a2 2 0 012-2z'),
        },
      ],
      show: isApprovalEnabled(user) && (admin || hasPermission(user, 'approval:read')),
    },
    {
      key: 'other',
      label: t('nav.other'),
      icon: (
        <svg className="w-4 h-4 nav-item__icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="6" cy="12" r="1.75" fill="currentColor" />
          <circle cx="12" cy="12" r="1.75" fill="currentColor" />
          <circle cx="18" cy="12" r="1.75" fill="currentColor" />
        </svg>
      ),
      items: [
        {
          key: 'agent-definitions',
          pathPrefix: '/agent-definitions',
          href: '/agent-definitions',
          label: t('nav.agentDefinitions'),
          icon: icon(
            'M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c.251.023.501.05.75.082m-.75-.082a24.301 24.301 0 014.5 0m-4.5 0v5.714c0 .597.237 1.17.659 1.591L14.25 14.5M13.5 21l-.75-3.5M10.5 21l.75-3.5M6 9h12',
          ),
        },
      ],
      show: admin,
    },
  ];

  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    'agent-resources': true,
    'runtime-resources': true,
    iam: true,
    operations: true,
    other: true,
  });

  useEffect(() => {
    setOpenGroups((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const group of groups) {
        if (!group.show) continue;
        if (pathActive(group.items.map((item) => item.pathPrefix), path) && !next[group.key]) {
          next[group.key] = true;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleGroup = (key: string) => {
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const warnFor = (item: NavItem): WarnBadgeLink[] | undefined => {
    if (item.key === 'instances' && instanceCreated === false) {
      return [{ label: missingLabel(t('nav.instances')), to: '/instances', openTarget: 'instanceCreate' }];
    }
    if (item.key === 'model-templates' && modelDefined === false) {
      return [{ label: missingLabel(t('nav.modelTemplates')), to: '/model-templates', openTarget: 'modelTemplateNew' }];
    }
    if (item.key === 'agent-templates' && agentDefined === false) {
      return [{ label: missingLabel(t('nav.agentTemplates')), to: '/agent-templates', openTarget: 'agentTemplateNew' }];
    }
    if (item.key === 'service-config-templates' && poolDefined === false) {
      return [{ label: missingLabel(t('nav.runtimeTemplates')), to: '/service-config-templates' }];
    }
    if (item.key === 'agent-definitions' && agentDefinitionDefined === false) {
      return [{ label: missingLabel(t('nav.agentDefinitions')), to: '/agent-definitions', openTarget: 'agentDefinitionNew' }];
    }
    return undefined;
  };

  const renderItem = (item: NavItem, nested = false) => {
    const active = isItemActive(item, path);
    const warnLinks = warnFor(item);
    return (
      <button
        key={item.key}
        type="button"
        onClick={() => navigate(item.href)}
        className={`nav-item ${nested ? 'nav-item--nested' : ''} ${active ? 'active' : ''}`}
        aria-current={active ? 'page' : undefined}
      >
        {item.icon}
        <span className="nav-item__label">{item.label}</span>
        {warnLinks && warnLinks.length > 0 ? <WarnBadge className="ml-auto" links={warnLinks} /> : null}
      </button>
    );
  };

  const instanceCount = instancesPage?.total ?? instancesPage?.items?.length ?? 0;
  const visibleGroups = groups.filter((group) => group.show && group.items.length > 0);

  return (
    <aside className="nav manager-nav" aria-label={zh ? '主导航' : 'Main navigation'}>
      {admin && (
        <div className="space-y-1">
          {topItems.map((item) => renderItem(item))}
        </div>
      )}

      {visibleGroups.map((group) => {
        const open = openGroups[group.key] ?? true;
        const active = pathActive(group.items.map((item) => item.pathPrefix), path);
        return (
          <div key={group.key} className="nav-subgroup">
            <button
              type="button"
              className={`nav-subgroup__toggle ${active ? 'active' : ''}`}
              onClick={() => toggleGroup(group.key)}
              aria-expanded={open}
            >
              {group.icon}
              <span className="nav-subgroup__label">{group.label}</span>
              <Chevron open={open} />
            </button>
            {open && (
              <div className="nav-subgroup__children space-y-1">
                {group.items.map((item) => renderItem(item, true))}
              </div>
            )}
          </div>
        );
      })}

      <div className="flex-1" />
      {admin && (
        <div className="nav-footer">
          <div className="nav-footer__row">
            <span className="nav-footer__label">{t('nav.instances')}</span>
            <span className="nav-footer__value">{instanceCount}</span>
          </div>
          <div className="nav-footer__row">
            <span className="nav-footer__label">manager</span>
            <span className="nav-footer__value">v0.1.0</span>
          </div>
        </div>
      )}
    </aside>
  );
}
