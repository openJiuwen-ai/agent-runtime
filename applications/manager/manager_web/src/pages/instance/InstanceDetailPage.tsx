import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { InstanceApi, ApiError } from '../../services/api';
import { useRouter } from '../../router';
import { bumpGuideRevision } from '../../stores/guideStore';
import { toast } from '../../stores/uiStore';
import { isValidHttpUrl } from '../../utils/url';
import { InstanceDetailPanel } from './instanceDetailPanel/instanceDetailPanel';
import { InstanceAccessPanel } from './instanceAccessPanel/InstanceAccessPanel';
import { InstanceAgentResourceTab } from './instanceResourcePanel/InstanceAgentResourceTab';
import { InstanceServiceResourceTab } from './instanceResourcePanel/InstanceServiceResourceTab';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { ClusterWorkspaceNavigation, type ClusterWorkspaceSection } from '../../components/ClusterWorkspaceNavigation';
import { ClusterOtherSettingsPanel } from './ClusterOtherSettingsPanel';
import './instanceDetailPanel/ConnectivityCard.css';

export type InstancePageTab = 'access' | 'agentResources' | 'serviceResources' | 'otherSettings' | 'status';

interface Props {
  instanceId: string;
  tab?: InstancePageTab;
}

const HOST_MAX = 512;

export function InstanceDetailPage({ instanceId, tab = 'access' }: Props) {
  const { t, i18n } = useTranslation();
  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);
  const componentStatus = useAsync(
    () => (tab === 'status' ? InstanceApi.componentStatus(instanceId) : Promise.resolve(null)),
    [instanceId, tab],
  );
  const { navigate } = useRouter();
  const [probing, setProbing] = useState(false);
  const [checkResult, setCheckResult] = useState('');
  const [savingHosts, setSavingHosts] = useState(false);

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
      title: zh ? '为集群选择可用的 Agent 资源' : 'Choose the Agent resources for this cluster',
      body: zh ? 'Agent 是可复用定义；只有在当前集群绑定并设置授权范围后，用户面才会向目标用户提供。' : 'Agents are reusable definitions and only become available to users after being bound and authorized for this cluster.',
    },
    serviceResources: {
      title: zh ? '为 Agent 选择实际运行资源' : 'Choose the runtime resources for Agents',
      body: zh ? '运行规格是可复用定义；只有在当前集群启用后，运行时才会参与调度。' : 'Runtime profiles are reusable definitions and only participate in scheduling after being enabled for this cluster.',
    },
    otherSettings: {
      title: zh ? '集中管理当前集群的运行策略' : 'Manage cluster policies in one place',
      body: zh ? '通过目录定位日志配置和配额管理，每项独立保存。目录跳转保留本页未保存的填写内容。' : 'Use the directory to find logging and quota settings. Save each section independently. Moving between sections retains unsaved edits.',
    },
  };
  const context = tabContext[tab] ?? tabContext.access;

  const saveConnectivity = async (patch: { gateway_host?: string; runtime_host?: string }) => {
    const gateway = (patch.gateway_host ?? instance.data?.gateway_host ?? '').trim();
    const runtime = (patch.runtime_host ?? instance.data?.runtime_host ?? '').trim();
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('instanceForm.gatewayHost'), invalid: !gateway },
      { label: t('instanceForm.runtimeHost'), invalid: !runtime },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceForm.fieldRequired', { field: missing.label }));
      return false;
    }
    const hostsToValidate: { value: string; field: string }[] = [
      { value: gateway, field: t('instanceForm.gatewayHost') },
      { value: runtime, field: t('instanceForm.runtimeHost') },
    ];
    for (const host of hostsToValidate) {
      if (!isValidHttpUrl(host.value)) {
        toast('warn', t('instanceForm.hostInvalid', { field: host.field }));
        return false;
      }
    }
    if (gateway.length > HOST_MAX || runtime.length > HOST_MAX) {
      toast('warn', zh ? '地址过长' : 'Host too long');
      return false;
    }
    setSavingHosts(true);
    try {
      await InstanceApi.update(instanceId, {
        gateway_host: gateway,
        runtime_host: runtime,
      });
      toast('success', t('success.saved'));
      await instance.reload();
      bumpGuideRevision();
      return true;
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
      return false;
    } finally {
      setSavingHosts(false);
    }
  };

  const checkConnectivity = async () => {
    if (probing) return;
    setProbing(true);
    setCheckResult('');
    try {
      const result = await InstanceApi.probeHealth();
      setCheckResult(
        zh
          ? `刷新完成：已探活 ${result.probed} 个地址（跳过 ${result.skipped}），并更新组件与 Pod 状态。`
          : `Refreshed: probed ${result.probed} endpoints (skipped ${result.skipped}) and updated component/pod status.`,
      );
    } catch (cause) {
      toast('danger', zh ? `连接检查失败：${cause instanceof ApiError ? cause.detail : (cause as Error).message}` : `Connectivity check failed: ${(cause as Error).message}`);
    } finally {
      // 探活失败也要刷新本地实例/组件数据（刷新语义，不因 probe 失败卡住旧状态）
      try {
        await instance.reload();
        await componentStatus.reload();
      } catch {
        /* reload 失败已由各自 hook 暴露；此处不二次 toast */
      }
      setProbing(false);
    }
  };

  const sections: Record<InstancePageTab, ClusterWorkspaceSection> = {
    access: 'access',
    agentResources: 'agent-resources',
    serviceResources: 'service-resources',
    otherSettings: 'other-settings',
    status: 'status',
  };
  const section = sections[tab];
  if (instance.loading && !instance.data) return <div className="card">{t('common.loading')}</div>;
  if (instance.error || !instance.data) {
    return (
      <div className="card text-danger" role="alert">
        {instance.error || t('common.empty')}
        <button className="btn ml-3" onClick={() => void instance.reload()}>
          {t('common.refresh')}
        </button>
        <button className="btn ml-2" onClick={() => navigate('/instances')}>
          {zh ? '返回集群管理' : 'Return to clusters'}
        </button>
      </div>
    );
  }

  return (
    <div className={`cluster-detail-page cluster-detail-page--${tab} flex min-w-0 flex-col gap-4`}>
      <ClusterWorkspaceNavigation
        instanceId={instanceId}
        name={instance.data?.jiuwenclaw_name ?? instanceId}
        section={section}
      />
      <div className="cluster-detail-body w-full min-w-0">
        {tab === 'otherSettings' ? (
          <ClusterOtherSettingsPanel instanceId={instanceId} />
        ) : (
          <>
            {tab === 'access' && <InstanceAccessPanel instanceId={instanceId} />}
            {tab === 'agentResources' && <InstanceAgentResourceTab instanceId={instanceId} />}
            {tab === 'serviceResources' && <InstanceServiceResourceTab instanceId={instanceId} />}
            {tab === 'status' && (
              <InstanceDetailPanel
                instance={instance}
                componentStatus={componentStatus.data}
                componentStatusLoading={componentStatus.loading}
                componentStatusError={componentStatus.error}
                onSaveConnectivity={saveConnectivity}
                savingConnectivity={savingHosts}
                onRefresh={() => void checkConnectivity()}
                probing={probing}
                checkResult={checkResult}
              />
            )}
          </>
        )}
      </div>
      <footer className="cluster-detail-footer">
        <WorkspaceNote title={context.title}>{context.body}</WorkspaceNote>
      </footer>
    </div>
  );
}
