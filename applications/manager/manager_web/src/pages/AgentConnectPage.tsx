import { useAuth } from '../auth/AuthContext';
import { bindingDraftKey, bindingDraftForCluster, emptyBindingDraft, readBindingDraft } from '../utils/agentDraft';
import { agentPreparationCluster, savedAgentBindingContext, templateFlowQuery, templateReturnPath } from '../utils/agentFlowContext';
import { clusterPagePath } from '../utils/clusterNavigation';
import { bumpGuideRevision } from '../stores/guideStore';
import { collectPages } from '../utils/overview';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentTemplateApi, InstanceAgentResourceApi, InstanceServiceResourceApi } from '../services/api';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { loadOverviewInstances } from './OverviewPage';
import { MatchExprEditor } from '../components/MatchExprEditor';
import { ConfigRefreshCostHint } from '../components/ConfigRefreshCostHint';
import { AGENT_RESOURCE_MATCH_FIELDS, parseMatchExpr, validateMatchExprModel } from '../utils/matchExpr';
import { WorkspaceNote } from '../components/WorkspaceNote';

export function AgentConnectPage({ id }: { id: string }) {
  const { user } = useAuth();
  const { params } = useRouter();
  const cluster = agentPreparationCluster(params) || '';
  const draftKey = bindingDraftKey(user?.user_id || '', id, cluster);
  return <AgentConnectForm key={draftKey} id={id} draftKey={draftKey} />;
}

function AgentConnectForm({ id, draftKey }: { id: string; draftKey: string }) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { navigate, params } = useRouter();
  const requestedCluster = agentPreparationCluster(params) || '';
  const returnTo = templateReturnPath(params.get('returnTo'));
  const demo = requestedCluster === 'mock-cluster-demo';
  const agent = useAsync(() => AgentTemplateApi.get(id), [id]);
  const instances = useAsync(loadOverviewInstances, []);
  const [draft] = useState(() => {
    try { return bindingDraftForCluster(readBindingDraft(window.sessionStorage, draftKey), requestedCluster); }
    catch { return bindingDraftForCluster({ ...emptyBindingDraft }, requestedCluster); }
  });
  const [instance, setInstance] = useState(draft.instance);
  const [name, setName] = useState(draft.name);
  const checks = useAsync(async () => {
    if (!instance || demo || instance === 'mock-cluster-demo') return null;
    const [bindings, runtimes] = await Promise.all([
      collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instance, { page, page_size })),
      InstanceServiceResourceApi.listInstanceResources(instance, { page: 1, page_size: 1, enabled: true }),
    ]);
    return { instance, existing: bindings.filter(row => row.ref_template_id === id).length, runtimeCount: runtimes.total };
  }, [instance, id, demo]);
  const [expr, setExpr] = useState(draft.expr);
  const [step, setStep] = useState(0);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [draftError, setDraftError] = useState(false);
  useEffect(() => {
    if (demo) return;
    try {
      if (saved || (!instance && !name && !expr)) window.sessionStorage.removeItem(draftKey);
      else window.sessionStorage.setItem(draftKey, JSON.stringify({ instance, name, expr }));
      setDraftError(false);
    } catch { setDraftError(true); }
  }, [draftKey, saved, instance, name, expr, demo]);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  function discardDraft() {
    setConfirmDiscard(false);
    setInstance(requestedCluster); setName(''); setExpr(''); setStep(0); setError('');
  }
  useEffect(() => {
    if (demo || saved || (!name && !expr)) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [saved, name, expr, demo]);

  const canContinue = !demo && !agent.loading && !agent.error && !instances.loading && !instances.error && !!instance && !!instances.data?.some(row => row.jiuwenclaw_id === instance) && !!name.trim() && !!agent.data?.enabled;
  const valid = canContinue && !checks.loading && !checks.error && checks.data?.instance === instance && !validateMatchExprModel(parseMatchExpr(expr), AGENT_RESOURCE_MATCH_FIELDS);
  function savedBindingPath() {
    const target = clusterPagePath(instance, 'agent-resources', savedAgentBindingContext(params, instance));
    return `${target}${target.includes('?') ? '&' : '?'}${new URLSearchParams({ focusTemplate: id })}`;
  }
  async function save() {
    if (!valid || saving || saved || instance === 'mock-cluster-demo') return;
    setSaving(true); setError('');
    try {
      await InstanceAgentResourceApi.create(instance, { ref_template_id: id, resource_name: name.trim(), match_exprs: [expr], enabled: true });
      bumpGuideRevision(); setSaved(true);
    } catch (e) { setError((e as Error).message); } finally { setSaving(false); }
  }
  if (demo) return <section className="card overview-empty"><h1>{tr('当前为演示集群', 'Demo cluster')}</h1><p>{tr('演示数据不会写入真实配置，请返回集群查看演示绑定。', 'Demo data cannot write real configuration. Return to the cluster to view sample bindings.')}</p><button className="btn primary" onClick={() => navigate('/instances/mock-cluster-demo/agent-resources')}>{tr('返回演示集群', 'Return to demo cluster')}</button></section>;
  return <div className="workspace-overview"><div className="page-header"><div><button className="btn ghost sm" onClick={() => navigate(returnTo)}>← {tr('Agent模板管理', 'Agent templates')}</button><h1 className="page-title">{tr('绑定集群', 'Bind to a cluster')}</h1><p className="page-subtitle">{agent.data?.template_name || '…'} · {tr('选择集群，配置模板在该集群中的名称和使用范围。', 'Choose a cluster, a local Agent name and its usage scope.')}</p></div><button className="btn" onClick={() => navigate(`/agent-templates/${encodeURIComponent(id)}${templateFlowQuery(params)}`)}>{tr('查看模板详情', 'View template')}</button></div>
    {(agent.error || instances.error) && <div role="alert" className="card"><p className="text-danger">{agent.error || instances.error}</p><button className="btn mt-3" onClick={() => { void agent.reload(); void instances.reload(); }}>{tr('重新加载', 'Reload')}</button></div>}
    {!saved && (name || expr) && <div className="template-context"><div><strong>{tr('绑定草稿', 'Binding draft')}</strong><p role="status">{draftError ? tr('无法暂存草稿，离开前请保存配置。', 'Draft storage is unavailable. Save before leaving.') : tr('输入已暂存于当前标签页，返回此模板可继续。尚未提交到服务器。', 'Draft kept in this tab for this template. Not submitted to the server.')}</p></div><button className="btn sm" disabled={saving} onClick={() => setConfirmDiscard(true)}>{tr('清除草稿', 'Discard draft')}</button></div>}
    {confirmDiscard && !saved && <section className="card" role="alert"><p>{tr('清除尚未保存的绑定配置？此操作会清空名称、实例和授权条件。', 'Discard this draft? The name, instance and access conditions will be cleared.')}</p><div className="flex gap-2"><button className="btn" onClick={() => setConfirmDiscard(false)}>{tr('继续编辑', 'Keep editing')}</button><button className="btn" disabled={saving} onClick={discardDraft}>{tr('确认清除', 'Confirm discard')}</button></div></section>}
    <div className="connect-steps">{[tr('选择集群', 'Choose cluster'), tr('使用范围', 'Usage scope'), tr('检查并保存', 'Review & save')].map((label, n) => <span className={step === n ? 'active' : ''} aria-current={step === n ? 'step' : undefined} key={label}>{n + 1}. {label}</span>)}</div>
    {saved ? <section className="card overview-empty" role="status"><h2>{tr('绑定配置已保存', 'Binding configuration saved')}</h2><p>{tr('请继续在集群配置工作台检查准入、运行资源和组件状态，再验证对话。', 'Continue in the cluster workspace to check admission, runtime resources and component status before testing a conversation.')}</p><div className="flex flex-wrap justify-center gap-2"><button className="btn primary" onClick={() => navigate(clusterPagePath(instance, 'setup', savedAgentBindingContext(params, instance)))}>{tr('继续配置集群', 'Continue cluster configuration')}</button><button className="btn" onClick={() => navigate(savedBindingPath())}>{tr('查看已保存的绑定', 'View saved binding')}</button></div></section> : <section className="card connect-form">
      {step === 0 && <><h2>{tr('将模板绑定到哪个集群？', 'Which cluster should use this template?')}</h2><label className="label" htmlFor="connect-instance">{tr('集群', 'Cluster')}</label><select id="connect-instance" className="input" value={instance} disabled={instances.loading || !!instances.error} onChange={e => setInstance(e.target.value)}><option value="">{tr('请选择集群', 'Select a cluster')}</option>{instances.data?.map(r => <option key={r.jiuwenclaw_id} value={r.jiuwenclaw_id}>{r.jiuwenclaw_name}</option>)}</select>{instances.loading && <p role="status">{tr('正在读取集群…', 'Loading clusters…')}</p>}{instance && !instances.loading && instances.data && !instances.data.some(row => row.jiuwenclaw_id === instance) && <p role="alert" className="text-danger">{tr('指定或暂存的集群已不可用，请重新选择。', 'The requested or saved cluster is unavailable. Select another cluster.')}</p>}{!instances.loading && !instances.error && instances.data?.length === 0 && <div className="overview-empty"><h3>{tr('还没有可绑定的集群', 'No clusters available')}</h3><p>{tr('先创建集群，完成后会自动带入当前模板继续绑定。', 'Create a cluster first. This template will be selected for the next binding step.')}</p><button className="btn primary" disabled={agent.loading || !!agent.error || !agent.data?.enabled} onClick={() => navigate(`/configuration-wizard?${new URLSearchParams({ template: id, returnTo })}`)}>{tr('新建集群并继续', 'Create cluster and continue')}</button></div>}<label className="label" htmlFor="connect-name">{tr('集群中的 Agent 名称', 'Agent name in this cluster')}</label><input id="connect-name" className="input" maxLength={128} value={name} onChange={e => setName(e.target.value)} placeholder={agent.data?.template_name} /><p className="text-sm text-muted">{tr('这是该集群内的绑定名称，不会修改原模板名称。', 'This names the cluster binding; the template name stays the same.')}</p></>}
      {step === 1 && <><h2>{tr('谁可以使用', 'Who can use this Agent')}</h2><p className="text-muted">{tr('范围只对已获得集群准入的身份生效，留空表示全部已准入身份。', 'Everyone means users already admitted to this instance. Admission is not granted automatically.')}</p><MatchExprEditor value={expr} onChange={setExpr} allowedFields={AGENT_RESOURCE_MATCH_FIELDS} /></>}
      {step === 2 && <><h2>{tr('确认绑定配置', 'Review binding configuration')}</h2><dl className="connect-summary"><dt>Agent</dt><dd>{agent.data?.template_name}</dd><dt>{tr('集群', 'Cluster')}</dt><dd>{instances.data?.find(r => r.jiuwenclaw_id === instance)?.jiuwenclaw_name}</dd><dt>{tr('资源名称', 'Resource name')}</dt><dd>{name}</dd><dt>{tr('授权表达式', 'Access expression')}</dt><dd>{expr || tr('集群内已准入的全体身份', 'All admitted users')}</dd></dl><p className="text-muted">{tr('本操作只保存 Agent 绑定。运行规格匹配和最终有效权限仍需在集群中核对。', 'This saves the Agent binding only. Verify runtime matching and effective access in the instance.')}</p>{checks.loading && <p role="status">{tr('正在检查集群配置…', 'Checking instance configuration…')}</p>}
      {checks.error && <div role="alert"><p className="text-danger">{checks.error}</p><button className="btn" onClick={() => void checks.reload()}>{tr('重试检查', 'Retry checks')}</button></div>}
      {checks.data?.instance === instance && <div className="binding-checks"><p>{tr('同一 Agent 已有绑定：', 'Existing bindings for this Agent: ')}{checks.data.existing}</p><p>{checks.data.runtimeCount ? tr('集群已有启用的运行规格，仍需核对授权条件能否命中。', 'Enabled runtime profiles exist; matching conditions still require verification.') : tr('集群没有启用的运行规格。可以先保存绑定，但还不能确认 Agent 可运行。', 'No enabled runtime profiles. You can save the binding, but execution is not confirmed.')}</p>{checks.data.existing > 0 && <p>{tr('再次保存会新增一条绑定，不会覆盖已有授权。', 'Saving adds a binding; it does not replace existing grants.')}</p>}</div>}
      <ConfigRefreshCostHint /></>}
      {agent.data && !agent.data.enabled && <p role="alert">{tr('此模板已停用，请返回模板管理启用后再绑定。', 'Enable this Agent definition before binding.')}</p>}
      {error && <p className="text-danger" role="alert">{error}</p>}
      <div className="connect-actions"><button className="btn" disabled={step === 0 || saving} onClick={() => setStep(step - 1)}>{tr('上一步', 'Back')}</button>{step < 2 ? <button className="btn primary" disabled={!canContinue || (step === 1 && !!validateMatchExprModel(parseMatchExpr(expr), AGENT_RESOURCE_MATCH_FIELDS))} onClick={() => setStep(step + 1)}>{tr('下一步', 'Next')}</button> : <button className="btn primary" disabled={!valid || saving} onClick={() => void save()}>{saving ? tr('正在保存…', 'Saving…') : tr('保存绑定配置', 'Save binding')}</button>}</div>
    </section>}
    <WorkspaceNote title={tr('保存绑定后，继续在集群工作台检查可用性', 'Check readiness in the cluster workspace after binding')}>
      {tr('模板定义能力组合；集群准入、运行资源和 Agent 使用范围共同决定能否使用。保存绑定不等于已完成运行部署，也不会自动授予集群准入。', 'The template defines capabilities. Admission, runtime resources and Agent scope together determine usability. Saving a binding does not confirm deployment or grant cluster admission.')}
    </WorkspaceNote>
  </div>;
}
