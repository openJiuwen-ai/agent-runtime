import { Component, ReactNode, useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Sidebar } from './components/Sidebar';
import { Toaster } from './components/Toaster';
import { ThemeToggle } from './components/ThemeToggle';
import { LanguageSwitcher } from './components/LanguageSwitcher';
import { ConfigGuideMenu } from './components/ConfigGuideMenu';
import { UserMenu } from './components/UserMenu';
import { ApprovalTodoButton } from './components/ApprovalTodoButton';
import { OverviewPage } from './pages/OverviewPage';
import { DocsPage } from './pages/DocsPage';
import { InstanceListPage } from './pages/instance/InstanceListPage';
import { InstanceDetailPage } from './pages/instance/InstanceDetailPage';
import { ModelTemplatesPage } from './pages/templates/ModelTemplatesPage';
import { EmbeddingTemplatesPage } from './pages/templates/EmbeddingTemplatesPage';
import { ExtensionTemplatesPage } from './pages/templates/ExtensionTemplatesPage';
import { McpTemplatesPage } from './pages/templates/McpTemplatesPage';
import { SkillPrebuiltTemplatesPage } from './pages/templates/SkillPrebuiltTemplatesPage';
import { ContainerTemplatesPage } from './pages/templates/ContainerTemplatesPage';
import { ServiceConfigTemplatesPage } from './pages/templates/ServiceConfigTemplatesPage';
import { SafetyGuardrailsPage } from './pages/templates/SafetyGuardrailsPage';
import { matchRoute, RouterProvider, useRouter } from './router';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { UsersPage } from './pages/iam/UsersPage';
import { OrgsPage } from './pages/iam/OrgsPage';
import { OrgsEditPage } from './pages/iam/OrgsEditPage';
import { RolesPage } from './pages/iam/RolesPage';
import { RolesEditPage } from './pages/iam/RolesEditPage';
import { ApprovalPage } from './pages/approval/ApprovalPage';
import { ApprovalEditPage } from './pages/approval/ApprovalEditPage';
import { AgentTemplatesPage } from './pages/templates/AgentTemplatesPage';
import { A2AManagementPage } from './pages/templates/A2AManagementPage';
import { getProductName } from './utils/env';
import {
  ApiError,
  AuthUser,
  canAccessManager,
  hasPermission,
  isPlatformAdmin,
  isWorkspaceQuotaEnabled,
  UserConsoleApi,
} from './services/api';

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('React Error:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-screen p-8">
          <div className="card max-w-xl">
            <div className="text-lg font-semibold text-danger mb-2">Application Error</div>
            <pre className="text-xs mono whitespace-pre-wrap text-muted">
              {this.state.error?.stack ?? this.state.error?.message ?? 'unknown'}
            </pre>
            <button className="btn primary mt-3" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function RouteView() {
  const { t } = useTranslation();
  const { path } = useRouter();
  const { user } = useAuth();
  const platformAdmin = isPlatformAdmin(user);

  if (!platformAdmin) {
    const approvalUiEnabled = isWorkspaceQuotaEnabled(user);
    if (path === '/approvals' && approvalUiEnabled && hasPermission(user, 'approval:read')) {
      return <ApprovalPage />;
    }
    const approvalEditLimited = matchRoute('/approvals/:orderNum', path);
    if (approvalEditLimited && approvalUiEnabled && hasPermission(user, 'approval:read')) {
      return <ApprovalEditPage orderNum={approvalEditLimited.orderNum} />;
    }
    if (hasPermission(user, 'iam:role:read')) {
      if (path === '/roles') {
        return <RolesPage />;
      }
      if (path === '/roles/new') {
        return <RolesEditPage />;
      }
      const roleEdit = matchRoute('/roles/:roleId', path);
      if (roleEdit) {
        return <RolesEditPage roleId={roleEdit.roleId} />;
      }
    }
    return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
  }

  if (path === '/overview' || path === '/') {
    return <OverviewPage />;
  }
  if (path === '/instances' || path === '/topology') {
    return <InstanceListPage />;
  }
  if (path === '/model-templates') {
    return <ModelTemplatesPage />;
  }
  if (path === '/embedding-templates') {
    return <EmbeddingTemplatesPage />;
  }
  if (path === '/extension-config-templates') {
    return <ExtensionTemplatesPage />;
  }
  if (path === '/mcp-templates') {
    return <McpTemplatesPage />;
  }
  if (path === '/skill-prebuilt-templates') {
    return <SkillPrebuiltTemplatesPage />;
  }
  if (path === '/container-templates') {
    return <ContainerTemplatesPage />;
  }
  if (path === '/safety-guardrails') {
    return <SafetyGuardrailsPage />;
  }
  if (path === '/a2a-management') {
    return <A2AManagementPage />;
  }
  if (path === '/service-config-templates') {
    return <ServiceConfigTemplatesPage />;
  }
  if (path === '/service-config-templates/new') {
    // 新建已改为列表页内弹窗；保留 /new URL 兼容引导页等跳转入口
    return <ServiceConfigTemplatesPage autoNew />;
  }
  const serviceConfigEdit = matchRoute('/service-config-templates/:templateId', path);
  if (serviceConfigEdit) {
    // 编辑已改为列表页内弹窗；保留 /:templateId URL 兼容历史链接
    return <ServiceConfigTemplatesPage autoEditId={serviceConfigEdit.templateId} />;
  }
  if (path === '/users') {
    return <UsersPage />;
  }
  if (path === '/orgs') {
    return <OrgsPage />;
  }
  if (path === '/orgs/new') {
    return <OrgsEditPage />;
  }
  const orgEdit = matchRoute('/orgs/:groupId', path);
  if (orgEdit) {
    return <OrgsEditPage groupId={orgEdit.groupId} />;
  }
  if (path === '/roles') {
    return <RolesPage />;
  }
  if (path === '/roles/new') {
    return <RolesEditPage />;
  }
  const roleEdit = matchRoute('/roles/:roleId', path);
  if (roleEdit) {
    return <RolesEditPage roleId={roleEdit.roleId} />;
  }
  if (path === '/approvals') {
    if (!isWorkspaceQuotaEnabled(user)) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <ApprovalPage />;
  }
  const approvalEdit = matchRoute('/approvals/:orderNum', path);
  if (approvalEdit) {
    if (!isWorkspaceQuotaEnabled(user)) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <ApprovalEditPage orderNum={approvalEdit.orderNum} />;
  }
  if (path === '/agent-templates') {
    return <AgentTemplatesPage />;
  }
  const instanceAccess = matchRoute('/instances/:id/access', path);
  if (instanceAccess) {
    return <InstanceDetailPage instanceId={instanceAccess.id} tab="access" />;
  }
  const instanceClusterConfig = matchRoute('/instances/:id/cluster-config', path);
  if (instanceClusterConfig) {
    return <InstanceDetailPage instanceId={instanceClusterConfig.id} tab="agentResources" />;
  }
  const instanceAgentResources = matchRoute('/instances/:id/agent-resources', path);
  if (instanceAgentResources) {
    return <InstanceDetailPage instanceId={instanceAgentResources.id} tab="agentResources" />;
  }
  const instanceServiceResources = matchRoute('/instances/:id/service-resources', path);
  if (instanceServiceResources) {
    return <InstanceDetailPage instanceId={instanceServiceResources.id} tab="serviceResources" />;
  }
  // 旧「实例资源/全局配置」页签 URL（已并入「集群配置」页签组，分别落子页签）
  const legacyResources = matchRoute('/instances/:id/resources', path);
  if (legacyResources) {
    return <InstanceDetailPage instanceId={legacyResources.id} tab="agentResources" />;
  }
  const legacyConfig = matchRoute('/instances/:id/config', path);
  if (legacyConfig) {
    return <InstanceDetailPage instanceId={legacyConfig.id} tab="config" />;
  }
  const instanceStatus = matchRoute('/instances/:id/status', path);
  if (instanceStatus) {
    return <InstanceDetailPage instanceId={instanceStatus.id} tab="status" />;
  }
  const instanceQuota = matchRoute('/instances/:id/quota', path);
  if (instanceQuota) {
    return <InstanceDetailPage instanceId={instanceQuota.id} tab="workspaceQuota" />;
  }
  const instanceWorkspaceQuota = matchRoute('/instances/:id/workspace-quota', path);
  if (instanceWorkspaceQuota) {
    return <InstanceDetailPage instanceId={instanceWorkspaceQuota.id} tab="workspaceQuota" />;
  }
  const instanceTokenQuota = matchRoute('/instances/:id/token-quota', path);
  if (instanceTokenQuota) {
    return <InstanceDetailPage instanceId={instanceTokenQuota.id} tab="tokenQuota" />;
  }
  const instanceCost = matchRoute('/instances/:id/cost', path);
  if (instanceCost) {
    return <InstanceDetailPage instanceId={instanceCost.id} tab="cost" />;
  }
  const instanceAudit = matchRoute('/instances/:id/audit', path);
  if (instanceAudit) {
    return <InstanceDetailPage instanceId={instanceAudit.id} tab="audit" />;
  }
  const detail = matchRoute('/instances/:id', path);
  if (detail) {
    return <InstanceDetailPage instanceId={detail.id} tab="access" />;
  }
  return <OverviewPage />;
}

function Shell() {
  const { t } = useTranslation();
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img src="/logo.svg" alt={getProductName()} className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">
              {t('brand.title')}
              <span className="brand-version">v0.1.0</span>
            </span>
            <span className="brand-sub">Manager</span>
          </div>
          <ConfigGuideMenu />
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              window.location.href = '/docs';
            }}
          >
            {t('docs.entry')}
          </button>
          <ApprovalTodoButton />
          <LanguageSwitcher />
          <ThemeToggle />
          <UserMenu />
        </div>
      </header>
      <Sidebar />
      <main className="content">
        <RouteView />
        <div className="ai-disclaimer">{t('footer.aiNotice')}</div>
      </main>
      <Toaster />
    </div>
  );
}

/** 已登录用户的默认落地页:平台/管理员类型角色→/manager,有限权限→对应页,否则→/user。 */
function roleHome(user: AuthUser): string {
  if (isPlatformAdmin(user) || user.manager_access) return '/manager';
  if (isWorkspaceQuotaEnabled(user) && hasPermission(user, 'approval:read')) {
    return '/manager/approvals';
  }
  if (hasPermission(user, 'iam:role:read')) return '/manager/roles';
  return '/user';
}

/** /auth:已登录则按角色跳走,否则展示登录页。 */
function AuthRoute() {
  const { user } = useAuth();
  if (user) return <Navigate to={roleHome(user)} replace />;
  return <LoginPage />;
}

/** 登录 + 角色守卫:未登录→/auth;要求管理面但无资格→/user。 */
function RequireAuth({ manager, children }: { manager?: boolean; children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/auth" replace />;
  if (manager && !canAccessManager(user)) {
    return <Navigate to="/user" replace />;
  }
  return <>{children}</>;
}

/** 根/未知路径:按登录态与角色重定向。 */
function RootRedirect() {
  const { user } = useAuth();
  return <Navigate to={user ? roleHome(user) : '/auth'} replace />;
}

function readCookie(name: string): string {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const item = part.trim();
    if (item.startsWith(prefix)) {
      return decodeURIComponent(item.slice(prefix.length));
    }
  }
  return '';
}

/**
 * /chat 由 nginx 按 Cookie jiuwenclaw_id 动态反代 User Web，不能使用 SPA 内部 Navigate。
 * 保留 /user 作为角色落地地址：先写入 active-cluster Cookie，再跳 /chat/。
 * 无 Agent 上下文时仍进入 /chat/，由 User Web EnterpriseEntry 展示空态与「返回登录页」。
 */
function UserWebRedirect() {
  const { t } = useTranslation();
  const { logout } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { contexts } = await UserConsoleApi.agentContexts();
        if (cancelled) return;
        if (!contexts.length) {
          // 无上下文：先清残留 HttpOnly jiuwenclaw_id，再交给 User Web 空态页
          // （否则上一用户选中的实例 Cookie 会让 auth_request 对当前用户 403）
          try {
            await UserConsoleApi.clearActiveCluster();
          } catch {
            /* 忽略 */
          }
          if (!cancelled) {
            window.location.replace('/chat/');
          }
          return;
        }
        const cookieJid = readCookie('jiuwenclaw_id');
        const preferred =
          contexts.find((item) => item.jiuwenclaw_id === cookieJid)?.jiuwenclaw_id ??
          contexts[0].jiuwenclaw_id;
        await UserConsoleApi.setActiveCluster(preferred);
        if (!cancelled) {
          window.location.replace('/chat/');
        }
      } catch (e) {
        if (!cancelled) {
          setError(
            e instanceof ApiError ? e.detail : (e as Error).message || t('auth.clusterSelectFailed'),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-3 text-muted">
        <div>{error}</div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => window.location.reload()}>
            {t('common.refresh')}
          </button>
          <button className="btn btn-primary" onClick={() => void logout()}>
            {t('auth.backToLogin')}
          </button>
        </div>
      </div>
    );
  }
  return <div className="flex items-center justify-center h-screen text-muted">{t('auth.loading')}</div>;
}

function Gate() {
  const { t } = useTranslation();
  const { ready } = useAuth();
  if (!ready) {
    return <div className="flex items-center justify-center h-screen text-muted">{t('auth.loading')}</div>;
  }
  return (
    <Routes>
      {/* 文档页：登录前后均可访问 */}
      <Route path="/docs" element={<DocsPage />} />
      {/* 认证面 */}
      <Route path="/auth" element={<AuthRoute />} />
      {/* 管理面(admin):内部页面在 /manager basename 下,既有页面零改动 */}
      <Route
        path="/manager/*"
        element={
          <RequireAuth manager>
            <RouterProvider basename="/manager">
              <Shell />
            </RouterProvider>
          </RequireAuth>
        }
      />
      {/* 用户面：通过页面级跳转进入同源 /chat，不再套 Manager Web iframe。 */}
      <Route
        path="/user/*"
        element={
          <RequireAuth>
            <UserWebRedirect />
          </RequireAuth>
        }
      />
      {/* 根/未知 → 按角色落地 */}
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </ErrorBoundary>
  );
}
