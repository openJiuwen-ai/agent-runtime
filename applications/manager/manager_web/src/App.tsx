import { Component, ReactNode, useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Sidebar } from './components/Sidebar';
import { Toaster } from './components/Toaster';
import { ThemeToggle } from './components/ThemeToggle';
import { LanguageSwitcher } from './components/LanguageSwitcher';
import { OverviewPage } from './pages/OverviewPage';
import { InstanceListPage } from './pages/instance/InstanceListPage';
import { InstanceDetailPage } from './pages/instance/InstanceDetailPage';
import { ModelTemplatesPage } from './pages/templates/ModelTemplatesPage';
import { EmbeddingTemplatesPage } from './pages/templates/EmbeddingTemplatesPage';
import { ExtensionTemplatesPage } from './pages/templates/ExtensionTemplatesPage';
import { SkillWhitelistTemplatesPage } from './pages/templates/SkillWhitelistTemplatesPage';
import { ServiceConfigTemplatesPage } from './pages/templates/ServiceConfigTemplatesPage';
import { ServiceConfigTemplateEditPage } from './pages/templates/ServiceConfigTemplateEditPage';
import { SafetyGuardrailsPage } from './pages/templates/SafetyGuardrailsPage';
import { matchRoute, RouterProvider, useRouter } from './router';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { UsersPage } from './pages/iam/UsersPage';
import { OrgsPage } from './pages/iam/OrgsPage';
import { AgentTemplatesPage } from './pages/templates/AgentTemplatesPage';
import { getProductName } from './utils/env';

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
  const { path } = useRouter();

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
  if (path === '/skill-whitelist-templates') {
    return <SkillWhitelistTemplatesPage />;
  }
  if (path === '/safety-guardrails') {
    return <SafetyGuardrailsPage />;
  }
  if (path === '/service-config-templates') {
    return <ServiceConfigTemplatesPage />;
  }
  if (path === '/service-config-templates/new') {
    return <ServiceConfigTemplateEditPage />;
  }
  const serviceConfigEdit = matchRoute('/service-config-templates/:templateId', path);
  if (serviceConfigEdit) {
    return <ServiceConfigTemplateEditPage templateId={serviceConfigEdit.templateId} />;
  }
  if (path === '/users') {
    return <UsersPage />;
  }
  if (path === '/orgs') {
    return <OrgsPage />;
  }
  if (path === '/agent-templates') {
    return <AgentTemplatesPage />;
  }
  const instanceAccess = matchRoute('/instances/:id/access', path);
  if (instanceAccess) {
    return <InstanceDetailPage instanceId={instanceAccess.id} tab="access" />;
  }
  const instanceResources = matchRoute('/instances/:id/resources', path);
  if (instanceResources) {
    return <InstanceDetailPage instanceId={instanceResources.id} tab="resources" />;
  }
  const instanceConfig = matchRoute('/instances/:id/config', path);
  if (instanceConfig) {
    return <InstanceDetailPage instanceId={instanceConfig.id} tab="config" />;
  }
  const instanceStatus = matchRoute('/instances/:id/status', path);
  if (instanceStatus) {
    return <InstanceDetailPage instanceId={instanceStatus.id} tab="status" />;
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
          <img src="/logo.png" alt={getProductName()} className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">
              {t('brand.title')}
              <span className="brand-version">v0.1.0</span>
            </span>
            <span className="brand-sub">{t('brand.sub')}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <UserMenu />
        </div>
      </header>
      <Sidebar />
      <main className="content">
        <RouteView />
      </main>
      <Toaster />
    </div>
  );
}

function UserMenu() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted">
        {user.display_name}
        <span className="badge ml-1">{user.is_admin ? t('iam.roleAdmin') : t('iam.roleUser')}</span>
      </span>
      <button className="btn" onClick={() => void logout()}>{t('auth.logout')}</button>
    </div>
  );
}

/** 已登录用户的默认落地页:管理员→/manager,普通用户→/user（随后进入同源 User Web）。 */
function roleHome(isAdmin: boolean): string {
  return isAdmin ? '/manager' : '/user';
}

/** /auth:已登录则按角色跳走,否则展示登录页。 */
function AuthRoute() {
  const { user } = useAuth();
  if (user) return <Navigate to={roleHome(user.is_admin)} replace />;
  return <LoginPage />;
}

/** 登录 + 角色守卫:未登录→/auth;要求 admin 但非 admin→/user。 */
function RequireAuth({ admin, children }: { admin?: boolean; children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/auth" replace />;
  if (admin && !user.is_admin) return <Navigate to="/user" replace />;
  return <>{children}</>;
}

/** 根/未知路径:按登录态与角色重定向。 */
function RootRedirect() {
  const { user } = useAuth();
  return <Navigate to={user ? roleHome(user.is_admin) : '/auth'} replace />;
}

/**
 * /chat 由 Manager Web 后端同源代理 User Web，不能使用 SPA 内部 Navigate。
 * 保留 /user 作为角色落地地址，避免认证回调和已有书签失效。
 */
function UserWebRedirect() {
  const { t } = useTranslation();
  useEffect(() => {
    window.location.replace('/chat/');
  }, []);
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
      {/* 认证面 */}
      <Route path="/auth" element={<AuthRoute />} />
      {/* 管理面(admin):内部页面在 /manager basename 下,既有页面零改动 */}
      <Route
        path="/manager/*"
        element={
          <RequireAuth admin>
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
