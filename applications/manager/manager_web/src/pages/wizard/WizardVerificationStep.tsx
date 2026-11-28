import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { StatusBadge } from '../../components/StatusBadge';
import { useAsync } from '../../hooks/useAsync';
import { AgentDefinitionApi, AgentTemplateApi, ApiError, InstanceAgentResourceApi, InstanceApi, InstanceBindingApi, InstanceServiceResourceApi, ServiceConfigTemplateApi, UserConsoleApi } from '../../services/api';
import { deriveClusterSetupProgress, isActiveConfiguration } from '../../utils/clusterSetupProgress';
import type { GuidedClusterStage } from '../../utils/clusterWizard';
import { collectPages } from '../../utils/overview';
import { activeWizardAgentContexts } from '../../utils/wizardResources';
import { WizardStepHeading } from './WizardStepHeading';

export function WizardVerificationStep({ instanceId, revision, onStage, onFinish }: { instanceId: string; revision: number; onStage: (step: GuidedClusterStage) => void; onFinish: () => void }) {
  const { i18n } = useTranslation();
  const { user } = useAuth();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [probing, setProbing] = useState(false);
  const [opening, setOpening] = useState(false);
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
  const contexts = useAsync(async () => {
    const result = await UserConsoleApi.agentContexts();
    const saved = check.data;
    return { contexts: saved ? activeWizardAgentContexts(result.contexts, instanceId, saved.activeAgents, saved.activeRuntimes, saved.definitions) : [], reason: result.empty_reason };
  }, [instanceId, revision, user?.user_id, check.data]);
  const data = check.data;
  const contextCount = contexts.data?.contexts.length || 0;
  const canOpen = !!data?.configurationComplete && !!data?.pairCount && data.connected && !contexts.loading && !contexts.error && contextCount > 0;

  async function probe() {
    if (probing) return;
    setProbing(true); setError(''); setMessage('');
    try {
      await InstanceApi.probeHealth();
      await check.reload();
      setMessage(tr('连接检查已完成，下方展示当前集群结果。', 'Connectivity check finished. Results for this cluster are shown below.'));
    } catch (cause) { setError(cause instanceof ApiError ? cause.detail : String(cause)); }
    finally { setProbing(false); }
  }
  async function openConsole() {
    if (!canOpen || opening) return;
    setOpening(true); setError('');
    // Open synchronously to retain browser popup permission, then authorize before navigation.
    const target = window.open('about:blank', '_blank');
    if (target) target.opener = null;
    try {
      const result = await UserConsoleApi.agentContexts();
      if (!data || !activeWizardAgentContexts(result.contexts, instanceId, data.activeAgents, data.activeRuntimes, data.definitions).length) {
        target?.close();
        await contexts.reload();
        setError(tr('当前账号权限已变化，请重新检查准入和 Agent 使用范围。', 'Your access changed. Review admission and Agent scope.')); return;
      }
      await UserConsoleApi.setActiveCluster(instanceId);
      if (target) target.location.replace('/chat/');
      else { setError(tr('浏览器阻止了新标签页，请允许弹出窗口后重试。', 'Allow popups in your browser and retry.')); return; }
      setMessage(tr('用户面已在新标签页打开。请用当前账号选择 Agent 并完成一次真实对话。', 'The user console opened in a new tab. Select an Agent and complete a real conversation with your current account.'));
    } catch (cause) { target?.close(); setError(cause instanceof ApiError ? cause.detail : String(cause)); }
    finally { setOpening(false); }
  }

  return <>
    <WizardStepHeading step={tr('第 5 步 · 检查验证', 'Step 5 · Verification')} title={tr('检查并验证', 'Check and verify')} description={tr('先核对保存结果、组件连接和当前账号的实际权限，再进入用户面完成一次对话。', 'Review saved settings, connectivity and current account access, then test a conversation in the user console.')} actions={<button type="button" className="btn" disabled={check.loading || probing} onClick={() => void probe()}>{probing ? tr('检查中…', 'Checking…') : tr('检查连接', 'Check connections')}</button>} />
    <section className="card wizard-card">
    {check.loading && !data && <p role="status" className="mt-4 text-muted">{tr('正在读取保存结果…', 'Loading saved configuration…')}</p>}
    {check.error && <div className="mt-4"><p className="text-danger" role="alert">{check.error}</p><button type="button" className="btn sm mt-2" onClick={() => void check.reload()}>{tr('重试读取配置', 'Retry loading settings')}</button></div>}
    {data && <div className="wizard-verification__checks">
      {([
        [2, data.admission.total > 0, tr('集群准入', 'Cluster admission'), tr(`${data.admission.users} 个用户、${data.admission.orgs} 个组织的有效允许规则。`, `${data.admission.users} user and ${data.admission.orgs} organization active allow rules.`)],
        [3, data.runtime.total > 0, tr('运行资源', 'Runtime resources'), tr(`${data.runtime.total} 个有效运行资源绑定。`, `${data.runtime.total} active runtime bindings.`)],
        [4, data.agents.total > 0 && data.pairCount > 0, tr('Agent 绑定与模板组合', 'Agent bindings and template pairs'), tr(`${data.agents.total} 个有效 Agent 绑定，${data.pairCount} 个对应的配置与运行时模板组合。`, `${data.agents.total} active Agent bindings and ${data.pairCount} matching configuration/runtime pairs.`)],
      ] as const).map(([step, ready, title, detail]) => <div key={step} className={'wizard-verification__check ' + (ready ? 'is-ready' : '')}><span className="wizard-verification__icon">{ready ? '✓' : '!'}</span><div><strong className="text-sm">{title}</strong><p>{detail}</p></div><button type="button" className="btn sm" onClick={() => onStage(step)}>{tr('查看与修改', 'Review and edit')}</button></div>)}
      <div className={'wizard-verification__check ' + (data.connected ? 'is-ready' : '')}><span className="wizard-verification__icon">{data.connected ? '✓' : '!'}</span><div><strong className="text-sm">{tr('组件连接', 'Component connectivity')}</strong><div className="wizard-verification__components">{[['Gateway', data.instance.gateway_status], ['Runtime', data.instance.runtime_status], ['User Web', data.instance.user_web_status]].map(([label, status]) => <span key={label}>{label}<StatusBadge status={status || 'pending'} /></span>)}</div><p>{tr('连接检查反映服务是否可达。服务地址有误时返回第一步修改。', 'Checks show whether services are reachable. Correct endpoints in the first step if needed.')}</p></div><button type="button" className="btn sm" onClick={() => onStage(1)}>{tr('连接配置', 'Connection settings')}</button></div>
      <div className={'wizard-verification__check ' + (contextCount && !contexts.error && !contexts.loading ? 'is-ready' : '')}><span className="wizard-verification__icon">{contextCount && !contexts.error && !contexts.loading ? '✓' : '!'}</span><div><strong className="text-sm">{tr('当前账号实际权限', 'Current account access')}</strong><p>{contexts.loading ? tr('正在查询当前账号…', 'Checking current account…') : contexts.error ? contexts.error : contextCount ? tr(`已匹配 ${contextCount} 个可用 Agent 使用上下文。`, `${contextCount} Agent contexts are available.`) : contexts.data?.reason === 'no_agent_template' ? tr('未匹配可用的 Agent 模板组合，请返回第四步确认配置模板和运行时模板。', 'No matching Agent template pair. Review the configuration and runtime templates in step 4.') : tr('当前账号尚未匹配可用 Agent，请检查集群准入以及运行资源和 Agent 的适用范围。', 'No Agent is available to this account. Review admission and runtime/Agent scopes.')}</p></div><button type="button" className="btn sm" disabled={contexts.loading} onClick={() => void contexts.reload()}>{tr('重新检查', 'Check again')}</button></div>
    </div>}
    <div className="wizard-verification__notice">{tr('最后一步：在用户面完成真实对话。仅保存配置或服务在线，还不能确认 Agent 请求成功。', 'Final check: complete a real conversation in the user console. Saved settings and online services alone do not confirm a successful Agent request.')}</div>
    {message && <p className="mt-3 text-sm text-muted" role="status">{message}</p>}{error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
    <div className="wizard-actions"><button type="button" className="btn" onClick={onFinish}>{tr('返回配置工作台', 'Return to workspace')}</button><button type="button" className="btn primary" disabled={!canOpen || opening || probing || check.loading} onClick={() => void openConsole()}>{opening ? tr('正在进入…', 'Opening…') : tr('前往用户面验证 ↗', 'Test in user console ↗')}</button></div>
    </section>
  </>;
}
