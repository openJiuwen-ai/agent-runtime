import { canonicalAgentPath } from './utils/agentTemplateNavigation';
import { AgentDetailPage } from './pages/AgentDetailPage';
import { AgentWorkspacePage } from './pages/AgentWorkspacePage';
import { AgentConnectPage } from './pages/AgentConnectPage';
import { ConfigurationWizardPage } from './pages/ConfigurationWizardPage';
import { Component, ReactNode } from 'react';
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
import { InstanceDetailPage, type InstancePageTab } from './pages/instance/InstanceDetailPage';
import { ClusterConfigWorkbenchPage } from './pages/instance/ClusterConfigWorkbenchPage';
import { ClusterSetupResumePage } from './pages/instance/ClusterSetupResumePage';
import { canonicalClusterSection, canonicalClusterPath } from './utils/clusterNavigation';
import { DeploymentListPage } from './pages/deployment/DeploymentListPage';
import { DeploymentDetailPage } from './pages/deployment/DeploymentDetailPage';
import { CreateDeploymentPage } from './pages/deployment/CreateDeploymentPage';
import { ModelTemplatesPage } from './pages/templates/ModelTemplatesPage';
import { EmbeddingTemplatesPage } from './pages/templates/EmbeddingTemplatesPage';
import { ExtensionTemplatesPage } from './pages/templates/ExtensionTemplatesPage';
import { McpTemplatesPage } from './pages/templates/McpTemplatesPage';
import { SkillPrebuiltTemplatesPage } from './pages/templates/SkillPrebuiltTemplatesPage';
import { ContainerTemplatesPage } from './pages/templates/ContainerTemplatesPage';
import { ServiceConfigTemplatesPage } from './pages/templates/ServiceConfigTemplatesPage';
import { AgentDefinitionsPage } from './pages/templates/AgentDefinitionsPage';
import { SafetyGuardrailsPage } from './pages/templates/SafetyGuardrailsPage';
import { ExecutionGuardrailsPage } from './pages/templates/ExecutionGuardrailsPage';
import { matchRoute, RouterProvider, useRouter } from './router';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { FederationCallbackPage } from './pages/FederationCallbackPage';
import { FederationSilentCallbackPage } from './pages/FederationSilentCallbackPage';
import { FederationLogoutPage } from './pages/FederationLogoutPage';
import { FederationSessionPage } from './pages/FederationSessionPage';
import { UsersPage } from './pages/iam/UsersPage';
import { OrgsPage } from './pages/iam/OrgsPage';
import { OrgsEditPage } from './pages/iam/OrgsEditPage';
import { RolesPage } from './pages/iam/RolesPage';
import { RolesEditPage } from './pages/iam/RolesEditPage';
import { ApprovalPage } from './pages/approval/ApprovalPage';
import { ApprovalEditPage } from './pages/approval/ApprovalEditPage';
import { ApprovalSkillPreviewPage } from './pages/approval/ApprovalSkillPreviewPage';
import { ResourceWorkspaceFrame } from './components/ResourceWorkspaceFrame';
import { WorkspaceNote } from './components/WorkspaceNote';
import { ExpertPackageTemplatesPage } from './pages/templates/ExpertPackageTemplatesPage';
import { getProductName } from './utils/env';
import {
  AuthUser,
  canAccessManager,
  hasPermission,
  isPlatformAdmin,
  isWorkspaceQuotaEnabled,
  isApprovalEnabled,
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
  const { path, params } = useRouter();
  const { user } = useAuth();
  const platformAdmin = isPlatformAdmin(user);

  // AuthorizationPage is retained for future use; old bookmarks return to an available page.
  if (path === '/authorization') {
    if (!platformAdmin && !hasPermission(user, 'iam:role:read')) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <Navigate to={platformAdmin ? '/manager/overview' : '/manager/roles'} replace />;
  }

  if (!platformAdmin) {
    const approvalUiEnabled = isApprovalEnabled(user);
    if (path === '/approvals' && approvalUiEnabled && hasPermission(user, 'approval:read')) {
      return <ApprovalPage />;
    }
    const approvalSkillPreviewLimited = matchRoute('/approvals/:orderNum/skill-preview', path);
    if (approvalSkillPreviewLimited && approvalUiEnabled && hasPermission(user, 'approval:read')) {
      return <ApprovalSkillPreviewPage orderNum={approvalSkillPreviewLimited.orderNum} />;
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

  const legacyAgentPath = canonicalAgentPath(path);
  if (legacyAgentPath) return <Navigate to={`/manager${legacyAgentPath}${params.size ? `?${params}` : ''}`} replace />;
  if (path === '/agent-templates') return <AgentWorkspacePage />;
  if (path === '/configuration-wizard') return <ConfigurationWizardPage />;
  const connect = matchRoute('/agent-templates/:id/connect', path);
  if (connect) return <AgentConnectPage key={connect.id} id={connect.id} />;
  const agentDetail = matchRoute('/agent-templates/:id', path);
  if (agentDetail) return <AgentDetailPage key={agentDetail.id} id={agentDetail.id} />;
  if (path === '/overview' || path === '/') {
    return <OverviewPage />;
  }
  if (path === '/instances' || path === '/topology') {
    return <InstanceListPage />;
  }
  if (path === '/model-templates') {
    return (
      <div className="manager-page">
        <ModelTemplatesPage />
      </div>
    );
  }
  if (path === '/embedding-templates') {
    return (
      <div className="manager-page">
        <EmbeddingTemplatesPage />
      </div>
    );
  }
  if (path === '/extension-config-templates') {
    return (
      <div className="manager-page">
        <ExtensionTemplatesPage />
      </div>
    );
  }
  if (path === '/mcp-templates') {
    return (
      <div className="manager-page">
        <McpTemplatesPage />
      </div>
    );
  }
  if (path === '/skill-prebuilt-templates') {
    return (
      <div className="manager-page">
        <SkillPrebuiltTemplatesPage />
      </div>
    );
  }
  if (path === '/container-templates') {
    return <ContainerTemplatesPage />;
  }
  if (path === '/safety-guardrails') {
    return (
      <div className="manager-page">
        <SafetyGuardrailsPage />
      </div>
    );
  }
  if (path === '/execution-guard-templates') {
    return <ResourceWorkspaceFrame><ExecutionGuardrailsPage /></ResourceWorkspaceFrame>;
  }
  // A2A 管理页签暂时隐藏：旧书签与残留入口显式回到总览，避免静默落到末尾 OverviewPage
  if (path === '/a2a-management') {
    return <Navigate to="/manager/overview" replace />;
  }
  if (path === '/expert-packages') {
    return <Navigate to={`/manager/expert-package-templates${params.size ? `?${params}` : ''}`} replace />;
  }
  if (path === '/expert-package-templates') {
    return <ResourceWorkspaceFrame><ExpertPackageTemplatesPage /></ResourceWorkspaceFrame>;
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
  if (path === '/agent-definitions') {
    return <AgentDefinitionsPage />;
  }
  if (path === '/users') {
    return <UsersPage />;
  }
  if (path === '/orgs') {
    return <OrgsPage />;
  }
  if (path === '/orgs/new') {
    return (
      <>
        <OrgsEditPage />
        <WorkspaceNote title={t('iam.orgsEditBannerTitle')}>{t('iam.orgsEditBannerText')}</WorkspaceNote>
      </>
    );
  }
  const orgEdit = matchRoute('/orgs/:groupId', path);
  if (orgEdit) {
    return (
      <>
        <OrgsEditPage groupId={orgEdit.groupId} />
        <WorkspaceNote title={t('iam.orgsEditBannerTitle')}>{t('iam.orgsEditBannerText')}</WorkspaceNote>
      </>
    );
  }
  if (path === '/roles') {
    return <RolesPage />;
  }
  if (path === '/roles/new') {
    return (
      <>
        <RolesEditPage />
        <WorkspaceNote title={t('roles.editBannerTitle')}>{t('roles.editBannerText')}</WorkspaceNote>
      </>
    );
  }
  const roleEdit = matchRoute('/roles/:roleId', path);
  if (roleEdit) {
    return (
      <>
        <RolesEditPage roleId={roleEdit.roleId} />
        <WorkspaceNote title={t('roles.editBannerTitle')}>{t('roles.editBannerText')}</WorkspaceNote>
      </>
    );
  }
  if (path === '/approvals') {
    if (!isApprovalEnabled(user)) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <ApprovalPage />;
  }
  const approvalSkillPreview = matchRoute('/approvals/:orderNum/skill-preview', path);
  if (approvalSkillPreview) {
    if (!isApprovalEnabled(user)) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <ApprovalSkillPreviewPage orderNum={approvalSkillPreview.orderNum} />;
  }
  const approvalEdit = matchRoute('/approvals/:orderNum', path);
  if (approvalEdit) {
    if (!isApprovalEnabled(user)) {
      return <div className="card text-sm text-muted">{t('auth.noPagePermission')}</div>;
    }
    return <ApprovalEditPage orderNum={approvalEdit.orderNum} />;
  }
  const legacyExperts = matchRoute('/instances/:id/expert-packages', path);
  if (legacyExperts) return <Navigate to={'/manager' + canonicalClusterPath(legacyExperts.id, 'expert-packages', params)} replace />;
  const clusterRoute = matchRoute('/instances/:id/:section', path) || matchRoute('/instances/:id', path);
  if (clusterRoute) {
    const section = canonicalClusterSection(clusterRoute.section, isWorkspaceQuotaEnabled(user));
    if (!section || clusterRoute.section !== section) return <Navigate to={'/manager' + canonicalClusterPath(clusterRoute.id, clusterRoute.section, params, isWorkspaceQuotaEnabled(user))} replace />;
    if (section === 'setup') return <ClusterSetupResumePage key={clusterRoute.id} instanceId={clusterRoute.id} />;
    if (section === 'cluster-config') return <ClusterConfigWorkbenchPage key={clusterRoute.id} instanceId={clusterRoute.id} />;
    const tabs: Record<string, InstancePageTab> = {
      access: 'access', 'service-resources': 'serviceResources', 'agent-resources': 'agentResources',
      'other-settings': 'otherSettings', status: 'status',
    };
    return <InstanceDetailPage key={clusterRoute.id + ':' + section} instanceId={clusterRoute.id} tab={tabs[section]} />;
  }
  if (path === '/deployments') {
    return <DeploymentListPage />;
  }
  if (path === '/deployments/new') {
    return <CreateDeploymentPage />;
  }
  const deploymentDetail = matchRoute('/deployments/:id', path);
  if (deploymentDetail) {
    return <DeploymentDetailPage deploymentId={deploymentDetail.id} />;
  }
  return <OverviewPage />;
}

function Shell() {
  const { t } = useTranslation();
  return (
    <div className="shell manager-workspace">
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
          {/* TODO: 暂时隐藏右上角文档入口，恢复时取消注释即可 */}
          {/* <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              window.location.href = '/docs';
            }}
          >
            {t('docs.entry')}
          </button> */}
          <ApprovalTodoButton />
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

function roleHome(user: AuthUser): string | null {
  return canAccessManager(user) ? '/manager' : null;
}

/** /auth:已登录则按角色跳走,否则展示登录页。 */
function AuthRoute() {
  const { user } = useAuth();
  const home = user && roleHome(user);
  if (home) return <Navigate to={home} replace />;
  return <LoginPage denied={Boolean(user)} />;
}

/** 登录 + 角色守卫:未登录→/auth;要求管理面但无资格→/auth。 */
function RequireAuth({ manager, children }: { manager?: boolean; children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/auth" replace />;
  if (manager && !canAccessManager(user)) {
    return <Navigate to="/auth" replace />;
  }
  return <>{children}</>;
}

/** 根/未知路径:按登录态与角色重定向。 */
function RootRedirect() {
  const { user } = useAuth();
  return <Navigate to={(user && roleHome(user)) || '/auth'} replace />;
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
      <Route path="/auth/federation/session" element={<FederationSessionPage />} />
      <Route path="/auth/federation/:connectionId/callback" element={<FederationCallbackPage />} />
      <Route path="/auth/federation/:connectionId/silent-callback" element={<FederationSilentCallbackPage />} />
      <Route path="/auth/federation/logout" element={<FederationLogoutPage />} />
      <Route path="/auth/federation/:connectionId/logout-callback" element={<FederationLogoutPage callback />} />
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
