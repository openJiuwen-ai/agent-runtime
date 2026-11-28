import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StatusBadge } from '../../components/StatusBadge';
import { useAsync } from '../../hooks/useAsync';
import { AgentDefinitionApi, AgentTemplateApi, ApiError, InstanceAgentResourceApi, InstanceApi, InstanceBindingApi, InstanceServiceResourceApi, ServiceConfigTemplateApi } from '../../services/api';
import { deriveClusterSetupProgress, isActiveConfiguration } from '../../utils/clusterSetupProgress';
import type { GuidedClusterStage } from '../../utils/clusterWizard';
import { collectPages } from '../../utils/overview';
import { WizardStepHeading } from './WizardStepHeading';

export function WizardVerificationStep({ instanceId, revision, onStage, onFinish }: { instanceId: string; revision: number; onStage: (step: GuidedClusterStage) => void; onFinish: () => void }) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const check = useAsync(async () => {
    const [instance, users, orgs, agents, runtimes, definitions, agentTemplates, runtimeTemplates] = await Promise.all([
      InstanceApi.get(instanceId), InstanceBindingApi.listUsers(instanceId), InstanceBindingApi.listOrgs(instanceId),
      collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size })),
      collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size })),
      collectPages((page, page_size) => AgentDefinitionApi.list({ page, page_size })),
      collectPages((page, page_size) => AgentTemplateApi.list({ page, page_size })),
      collectPages((page, page_size) => ServiceConfigTemplateApi.list({ page, page_size })),
    ]);
    const enabledAgentIds = new Set(agentTemplates.filter(row => row.enabled).map(row => row.template_id));
    const enabledRuntimeIds = new Set(runtimeTemplates.filter(row => row.enabled).map(row => row.template_id));
    const activeAgents = agents.filter(row => isActiveConfiguration(row) && enabledAgentIds.has(row.ref_template_id));
    const activeRuntimes = runtimes.filter(row => isActiveConfiguration(row) && enabledRuntimeIds.has(row.ref_template_id));
    const progress = deriveClusterSetupProgress({ instance, users: users.items, orgs: orgs.items, agents: activeAgents, runtimes: activeRuntimes });
    const pairCount = definitions.filter(row => activeAgents.some(agent => agent.ref_template_id === row.agent_config_template_id) && activeRuntimes.some(runtime => runtime.ref_template_id === row.runtime_template_id)).length;
    return { ...progress, pairCount, activeAgents, activeRuntimes, definitions };
  }, [instanceId, revision]);
  const data = check.data;

  async function probe() {
    if (probing) return;
    setProbing(true); setError(''); setMessage('');
    try {
      await InstanceApi.probeHealth();
      await check.reload();
      setMessage(tr('已发起连接检查，请核对下方重新读取的结果；读取失败时请重试。', 'Connection checks were requested. Review the refreshed results below and retry if loading fails.'));
    } catch (cause) { setError(cause instanceof ApiError ? cause.detail : String(cause)); }
    finally { setProbing(false); }
  }
  return <>
    <WizardStepHeading step={tr('第 5 步 · 检查验证', 'Step 5 · Verification')} title={tr('检查并验证', 'Check and verify')} description={tr('核对保存结果和组件连接，再使用测试账号登录独立用户面验证。', 'Review saved settings and connectivity, then sign in to the separate user console with a test account.')} actions={<button type="button" className="btn" disabled={check.loading || probing} onClick={() => void probe()}>{probing ? tr('检查中…', 'Checking…') : tr('检查连接', 'Check connections')}</button>} />
    <section className="card wizard-card">
    {check.loading && !data && <p role="status" className="mt-4 text-muted">{tr('正在读取保存结果…', 'Loading saved configuration…')}</p>}
    {check.error && <div className="mt-4"><p className="text-danger" role="alert">{check.error}</p><button type="button" className="btn sm mt-2" onClick={() => void check.reload()}>{tr('重试读取配置', 'Retry loading settings')}</button></div>}
    {data && <div className="wizard-verification__checks">
      {([
        [2, data.admission.total > 0, tr('集群准入', 'Cluster admission'), tr(`${data.admission.users} 个用户、${data.admission.orgs} 个组织的有效允许规则。`, `${data.admission.users} user and ${data.admission.orgs} organization active allow rules.`)],
        [3, data.runtime.total > 0, tr('运行资源', 'Runtime resources'), tr(`${data.runtime.total} 个有效运行资源绑定。`, `${data.runtime.total} active runtime bindings.`)],
        [4, data.agents.total > 0 && data.pairCount > 0, tr('Agent 绑定与模板组合', 'Agent bindings and template pairs'), tr(`${data.agents.total} 个有效 Agent 绑定，${data.pairCount} 个对应的配置与运行时模板组合。`, `${data.agents.total} active Agent bindings and ${data.pairCount} matching configuration/runtime pairs.`)],
      ] as const).map(([step, ready, title, detail]) => <div key={step} className={'wizard-verification__check ' + (ready ? 'is-ready' : '')}><span className="wizard-verification__icon">{ready ? '✓' : '!'}</span><div><strong className="text-sm">{title}</strong><p>{detail}</p></div><button type="button" className="btn sm" onClick={() => onStage(step)}>{tr('查看与修改', 'Review and edit')}</button></div>)}
      <div className={'wizard-verification__check ' + (data.connected ? 'is-ready' : '')}><span className="wizard-verification__icon">{data.connected ? '✓' : '!'}</span><div><strong className="text-sm">{tr('组件连接', 'Component connectivity')}</strong><div className="wizard-verification__components">{[['Gateway', data.instance.gateway_status], ['Runtime', data.instance.runtime_status]].map(([label, status]) => <span key={label}>{label}<StatusBadge status={status || 'pending'} /></span>)}</div><p>{tr('连接检查反映服务是否可达。服务地址有误时返回第一步修改。', 'Checks show whether services are reachable. Correct endpoints in the first step if needed.')}</p></div><button type="button" className="btn sm" onClick={() => onStage(1)}>{tr('连接配置', 'Connection settings')}</button></div>

    </div>}
    <div className="wizard-verification__notice">{tr('请从独立的用户面入口登录测试账号，选择此集群和 Agent，验证权限并完成一次真实对话。管理面与用户面使用各自的登录状态。', 'Sign in with a test account at the separate user console, then select this cluster and an Agent to verify access and complete a real conversation. Management and user sessions are separate.')}</div>
    {message && <p className="mt-3 text-sm text-muted" role="status">{message}</p>}{error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
    <div className="wizard-actions"><button type="button" className="btn" onClick={onFinish}>{tr('返回配置工作台', 'Return to workspace')}</button></div>
    </section>
  </>;
}
