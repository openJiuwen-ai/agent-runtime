import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useFormDirty } from '../../hooks/useFormDirty';
import { InstanceApi, UserConsoleApi, ApiError } from '../../services/api';
import { useRouter } from '../../router';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { bumpGuideRevision } from '../../stores/guideStore';
import { toast } from '../../stores/uiStore';
import { isValidHttpUrl } from '../../utils/url';
import { InstanceDetailPanel } from './instanceDetailPanel/instanceDetailPanel';
import { InstanceAccessPanel } from './instanceAccessPanel/InstanceAccessPanel';
import { InstanceAgentResourceTab } from './instanceResourcePanel/InstanceAgentResourceTab';
import { InstanceServiceResourceTab } from './instanceResourcePanel/InstanceServiceResourceTab';
import { InstanceExpertPackagesTab } from './instanceResourcePanel/InstanceExpertPackagesTab';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { isMockInstanceId, mockInstanceDetail } from '../../config/mockManager';
import { ClusterWorkspaceNavigation, ClusterWorkspaceReturnAction, type ClusterWorkspaceSection } from '../../components/ClusterWorkspaceNavigation';
import { MockClusterTabPanel } from './MockClusterTabPanel';
import { ClusterOtherSettingsPanel } from './ClusterOtherSettingsPanel';
import { clusterPagePath } from '../../utils/clusterNavigation';

export type InstancePageTab = 'access' | 'agentResources' | 'expertPackages' | 'serviceResources' | 'otherSettings' | 'status';

interface Props {
  instanceId: string;
  tab?: InstancePageTab;
}

const HOST_MAX = 512;

type ConnectivityForm = {
  gateway_host: string;
  runtime_host: string;
  user_web_host: string;
  gateway_web_http_host: string;
  gateway_web_ws_host: string;
};

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && (
        <span className="text-danger ml-0.5" aria-hidden="true">
          *
        </span>
      )}
    </label>
  );
}

export function InstanceDetailPage({ instanceId, tab = 'access' }: Props) {
  const { t, i18n } = useTranslation();
  const instance = useAsync(() => isMockInstanceId(instanceId) ? Promise.resolve(mockInstanceDetail) : InstanceApi.get(instanceId), [instanceId]);
  const mock = isMockInstanceId(instanceId);
  const { navigate, params } = useRouter();
  const [probing, setProbing] = useState(false);
  const [openingConsole, setOpeningConsole] = useState(false);
  const [checkResult, setCheckResult] = useState('');

  const [editOpen, setEditOpen] = useState(false);
  const [connectivityForm, setConnectivityForm] = useState<ConnectivityForm>({
    gateway_host: '',
    runtime_host: '',
    user_web_host: '',
    gateway_web_http_host: '',
    gateway_web_ws_host: '',
  });
  const { markClean, isDirty } = useFormDirty(editOpen);

  const zh = i18n.language.startsWith('zh');
  const tabContext: Record<string, { title: string; body: string }> = {
    access: {
      title: zh ? '先决定谁能进入这个集群' : 'Start with who can enter this cluster',
      body: zh ? '选择可以进入当前集群的用户或组织，两者至少配置一种。保存后先配置运行资源，再绑定 Agent；已保存配置可随时重新查看和修改。' : 'Allow users or organizations into this cluster, then configure runtime resources and Agent bindings. Saved settings can be reviewed at any time.',
    },
    status: {
      title: zh ? '确认连接状态，再验证配置是否可用' : 'Confirm connectivity before validating configuration',
      body: zh ? '探活只代表管理面能访问组件；Agent 是否能运行，还要同时满足准入、Agent 授权和运行资源条件。' : 'Health checks show management connectivity. Agent execution also requires admission, Agent access and runtime resources.',
    },
    agentResources: {
      title: zh ? '决定谁能使用哪些 Agent' : 'Choose who can use each Agent',
      body: zh ? '这里保存的是当前集群的 Agent 使用授权，不会修改全局 Agent 定义。' : 'These are Agent access bindings for this cluster; the global Agent definition remains unchanged.',
    },
    serviceResources: {
      title: zh ? '为 Agent 选择实际运行资源' : 'Choose the runtime resources for Agents',
      body: zh ? '运行规格是可复用定义；只有在当前集群启用后，运行时才会参与调度。' : 'Runtime profiles are reusable definitions and only participate in scheduling after being enabled for this cluster.',
    },
    expertPackages: {
      title: zh ? '为当前集群控制可安装的专家包' : 'Control expert packages for this cluster',
      body: zh ? '专家包按 Agent 模板或 Agent 分组授权，保存后由当前集群的运行环境读取。' : 'Grant packages by Agent template or group; the current cluster runtime reads the saved allowlist.',
    },
    otherSettings: {
      title: zh ? '集中管理当前集群的运行策略' : 'Manage cluster policies in one place',
      body: zh ? '通过左侧目录定位日志配置和配额管理，每项独立保存；成本统计与业务审计尚未开放。目录切换保留本页未保存的填写内容。' : 'Use the directory to find logging and quota settings. Save each section independently; cost and business audit are not available yet. Moving between sections retains unsaved edits.',
    },
  };
  const context = tabContext[tab] ?? tabContext.access;

  const handleOpenEditConnectivity = () => {
    const d = instance.data;
    const next: ConnectivityForm = {
      gateway_host: d?.gateway_host ?? '',
      runtime_host: d?.runtime_host ?? '',
      user_web_host: d?.user_web_host ?? '',
      gateway_web_http_host: d?.gateway_web_http_host ?? '',
      gateway_web_ws_host: d?.gateway_web_ws_host ?? '',
    };
    setConnectivityForm(next);
    markClean(next);
    setEditOpen(true);
  };

  const submitConnectivity = async () => {
    const gateway = connectivityForm.gateway_host.trim();
    const runtime = connectivityForm.runtime_host.trim();
    const userWeb = connectivityForm.user_web_host.trim();
    const gatewayWebHttp = connectivityForm.gateway_web_http_host.trim();
    const gatewayWebWs = connectivityForm.gateway_web_ws_host.trim();
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('instanceForm.gatewayHost'), invalid: !gateway },
      { label: t('instanceForm.runtimeHost'), invalid: !runtime },
      { label: t('instanceForm.userWebHost'), invalid: !userWeb },
      { label: t('instanceForm.gatewayWebHttpHost'), invalid: !gatewayWebHttp },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceForm.fieldRequired', { field: missing.label }));
      return;
    }
    const hostsToValidate: { value: string; field: string }[] = [
      { value: gateway, field: t('instanceForm.gatewayHost') },
      { value: runtime, field: t('instanceForm.runtimeHost') },
      { value: userWeb, field: t('instanceForm.userWebHost') },
      { value: gatewayWebHttp, field: t('instanceForm.gatewayWebHttpHost') },
      { value: gatewayWebWs, field: t('instanceForm.gatewayWebWsHost') },
    ];
    for (const host of hostsToValidate) {
      if (!host.value) continue;
      if (!isValidHttpUrl(host.value)) {
        toast('warn', t('instanceForm.hostInvalid', { field: host.field }));
        return;
      }
    }
    try {
      await InstanceApi.update(instanceId, {
        gateway_host: gateway,
        runtime_host: runtime,
        user_web_host: userWeb,
        gateway_web_http_host: gatewayWebHttp,
        gateway_web_ws_host: gatewayWebWs,
      });
      toast('success', t('success.saved'));
      setEditOpen(false);
      void instance.reload();
      bumpGuideRevision();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  const checkConnectivity = async () => {
    if (probing || mock) return;
    setProbing(true);
    setCheckResult('');
    try {
      const result = await InstanceApi.probeHealth();
      await instance.reload();
      setCheckResult(zh ? `检查结束：共检查 ${result.probed} 个组件地址，跳过 ${result.skipped} 个。当前集群的实际结果见下方组件状态。` : `Checked ${result.probed} component endpoints; skipped ${result.skipped}. Review this cluster's component status below.`);
    } catch (cause) {
      toast('danger', zh ? `连接检查失败：${cause instanceof ApiError ? cause.detail : (cause as Error).message}` : `Connectivity check failed: ${(cause as Error).message}`);
    } finally { setProbing(false); }
  };

  const openUserConsole = async () => {
    if (openingConsole || mock) return;
    setOpeningConsole(true);
    setCheckResult('');
    try {
      const { contexts } = await UserConsoleApi.agentContexts();
      if (!contexts.some(row => row.jiuwenclaw_id === instanceId)) {
        setCheckResult(zh ? '当前登录身份没有此集群的可用 Agent 上下文。请核对该身份的准入和 Agent 使用范围，或使用已获准的测试账号登录用户面。' : 'This identity has no Agent context in this cluster. Check admission and Agent scope, or sign into the user console with an admitted test account.');
        return;
      }
      await UserConsoleApi.setActiveCluster(instanceId);
      window.location.assign('/chat/');
    } catch (cause) {
      toast('danger', zh ? `无法进入当前集群用户面：${cause instanceof ApiError ? cause.detail : (cause as Error).message}` : `Could not open user console: ${(cause as Error).message}`);
    } finally { setOpeningConsole(false); }
  };

  const sections: Record<InstancePageTab, ClusterWorkspaceSection> = {
    access: 'access', agentResources: 'agent-resources', expertPackages: 'expert-packages',
    serviceResources: 'service-resources', otherSettings: 'other-settings', status: 'status',
  };
  const section = sections[tab];
  if (instance.loading && !instance.data) return <div className="card">{t('common.loading')}</div>;
  if (instance.error || !instance.data) return <div className="card text-danger" role="alert">{instance.error || t('common.empty')}<button className="btn ml-3" onClick={() => void instance.reload()}>{t('common.refresh')}</button><button className="btn ml-2" onClick={() => navigate('/instances')}>{zh ? '返回集群管理' : 'Return to clusters'}</button></div>;

  return (
    <>
      <div className="flex min-w-0 shrink-0 flex-col gap-4">
        <ClusterWorkspaceNavigation
          instanceId={instanceId}
          name={instance.data?.jiuwenclaw_name ?? instanceId}
          section={section}
        />
        <div className="w-full min-w-0 shrink-0">
          {tab === 'agentResources' && <div className="flex flex-wrap items-center justify-end gap-2 mb-3"><button className="btn sm" onClick={() => navigate(clusterPagePath(instanceId, 'expert-packages', params))}>{zh ? '专家包管理' : 'Expert packages'} →</button></div>}
          {tab === 'expertPackages' && <button className="btn ghost sm mb-3" onClick={() => navigate(clusterPagePath(instanceId, 'agent-resources', params))}>← {zh ? '返回 Agent绑定' : 'Back to Agent bindings'}</button>}
          {tab === 'otherSettings' ? <ClusterOtherSettingsPanel instanceId={instanceId} /> : mock ? <MockClusterTabPanel tab={tab} /> : <>
          {tab === 'access' && <InstanceAccessPanel instanceId={instanceId} />}
          {tab === 'agentResources' && (
            <InstanceAgentResourceTab instanceId={instanceId} />
          )}
          {tab === 'expertPackages' && <InstanceExpertPackagesTab instanceId={instanceId} />}
          {tab === 'serviceResources' && <InstanceServiceResourceTab instanceId={instanceId} />}
          {tab === 'status' && (
            <InstanceDetailPanel
              instance={instance}
              onOpenEditConnectivity={handleOpenEditConnectivity}
              onRefresh={() => void instance.reload()}
              onCheckConnectivity={() => void checkConnectivity()}
              onOpenUserConsole={() => void openUserConsole()}
              probing={probing}
              openingConsole={openingConsole}
              checkResult={checkResult}
            />
          )}
          </>}
        </div>
        <ClusterWorkspaceReturnAction instanceId={instanceId} />
        <WorkspaceNote title={context.title}>{context.body}</WorkspaceNote>
      </div>

      <Modal
        open={editOpen}
        title={t('instanceDetail.editConnectivity')}
        onClose={() => setEditOpen(false)}
        dirty={isDirty(connectivityForm)}
        size="lg"
        footer={
          <>
            <ModalCancelButton />
            <button className="btn primary" onClick={() => void submitConnectivity()}>
              {t('common.save')}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div>
            <FieldLabel required>{t('instanceForm.gatewayHost')}</FieldLabel>
            <LimitedTextInput
              value={connectivityForm.gateway_host}
              maxLength={HOST_MAX}
              onChange={(gateway_host) =>
                setConnectivityForm((prev) => ({ ...prev, gateway_host }))
              }
            />
          </div>
          <div>
            <FieldLabel required>{t('instanceForm.runtimeHost')}</FieldLabel>
            <LimitedTextInput
              value={connectivityForm.runtime_host}
              maxLength={HOST_MAX}
              onChange={(runtime_host) =>
                setConnectivityForm((prev) => ({ ...prev, runtime_host }))
              }
            />
          </div>
          <div>
            <FieldLabel required>{t('instanceForm.userWebHost')}</FieldLabel>
            <LimitedTextInput
              value={connectivityForm.user_web_host}
              maxLength={HOST_MAX}
              onChange={(user_web_host) =>
                setConnectivityForm((prev) => ({ ...prev, user_web_host }))
              }
            />
          </div>
          <div>
            <FieldLabel required>{t('instanceForm.gatewayWebHttpHost')}</FieldLabel>
            <LimitedTextInput
              value={connectivityForm.gateway_web_http_host}
              maxLength={HOST_MAX}
              onChange={(gateway_web_http_host) =>
                setConnectivityForm((prev) => ({ ...prev, gateway_web_http_host }))
              }
            />
          </div>
          <div>
            <FieldLabel>{t('instanceForm.gatewayWebWsHost')}</FieldLabel>
            <LimitedTextInput
              value={connectivityForm.gateway_web_ws_host}
              maxLength={HOST_MAX}
              onChange={(gateway_web_ws_host) =>
                setConnectivityForm((prev) => ({ ...prev, gateway_web_ws_host }))
              }
            />
          </div>
        </div>
      </Modal>
    </>
  );
}
