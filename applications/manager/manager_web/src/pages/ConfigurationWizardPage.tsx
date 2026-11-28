import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { AgentTemplateApi, ApiError, InstanceApi, ServiceConfigTemplateApi } from '../services/api';
import { toast } from '../stores/uiStore';
import { bumpGuideRevision } from '../stores/guideStore';
import { templateReturnPath } from '../utils/agentFlowContext';
import { guidedClusterStage, guidedClusterWizardPath, legacyClusterWizardPath } from '../utils/clusterWizard';
import { GuidedClusterSetup } from './wizard/GuidedClusterSetup';
import { WizardStepHeading } from './wizard/WizardStepHeading';
import {
  clearClusterCreationDraft, clusterCreationDraftKey, clusterCreationHosts,
  emptyClusterCreationDraft, readClusterCreationDraft, saveClusterCreationDraft,
  resolveClusterCreationHosts, updateClusterCreationNamespace, updateClusterCreationEndpoint,
  type ClusterCreationDraft,
} from '../utils/clusterCreationDraft';
import { isValidHttpUrl } from '../utils/url';
import { findUnsafeTextField } from '../utils/safeText';

/** A saved instance ID in the URL makes the guided flow resumable without recreating it. */
export function ConfigurationWizardPage() {
  const { navigate, params } = useRouter();
  const { user } = useAuth();
  const { i18n } = useTranslation();
  const legacyTarget = legacyClusterWizardPath(params);
  const template = params.get('template') || params.get('templateId') || '';
  const returnTo = params.has('returnTo') ? templateReturnPath(params.get('returnTo')) : '';
  const draftKey = clusterCreationDraftKey(user?.user_id || '', template, returnTo);

  useEffect(() => {
    if (legacyTarget) navigate(legacyTarget, { replace: true });
  }, [legacyTarget, navigate]);

  if (legacyTarget) return <div className="card">{i18n.language.startsWith('zh') ? '正在恢复这个集群的配置…' : 'Restoring this cluster configuration…'}</div>;
  const instanceId = params.get('instance');
  const stage = guidedClusterStage(params.get('stage'));
  if (instanceId && stage) return <GuidedClusterSetup key={instanceId} instanceId={instanceId} stage={stage} />;
  return <ClusterCreationForm key={draftKey} draftKey={draftKey} template={template} returnTo={returnTo} />;
}

function ClusterCreationForm({ draftKey, template, returnTo }: { draftKey: string; template: string; returnTo: string }) {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const { navigate } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  const [draft, setDraft] = useState<ClusterCreationDraft>(() => {
    try { return readClusterCreationDraft(sessionStorage, draftKey); }
    catch { return { ...emptyClusterCreationDraft }; }
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const requestInFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const templateState = useAsync(() => template ? AgentTemplateApi.get(template) : Promise.resolve(null), [template]);
  const deploymentDefaults = useAsync(() => ServiceConfigTemplateApi.deploymentDefaults(), []);
  const deployedNamespace = deploymentDefaults.data?.namespaces?.length === 1 ? deploymentDefaults.data.namespaces[0] : '';
  const hosts = useMemo(() => resolveClusterCreationHosts(draft), [draft]);
  const update = (patch: Partial<ClusterCreationDraft>) => { setError(''); setDraft(value => ({ ...value, ...patch })); };
  const updateEndpoint = (key: keyof typeof hosts, value: string) => {
    setError('');
    setDraft(draft => updateClusterCreationEndpoint(draft, key, value));
  };
  const updateNamespace = (namespace: string) => {
    setError('');
    setDraft(value => updateClusterCreationNamespace(value, namespace));
  };

  useEffect(() => {
    try { saveClusterCreationDraft(sessionStorage, draftKey, draft); } catch { /* Storage availability does not affect Manager CRUD. */ }
  }, [draft, draftKey]);

  const endpointFields = [
    { key: 'gateway' as const, label: tr('Gateway 管理地址', 'Gateway management URL'), required: true },
    { key: 'runtime' as const, label: tr('Runtime 管理地址', 'Runtime management URL'), required: true },
    { key: 'web' as const, label: tr('用户面地址', 'User Web URL'), required: true },
    { key: 'gatewayHttp' as const, label: tr('Gateway 用户面 HTTP 地址', 'Gateway user HTTP URL'), required: true },
    { key: 'gatewayWs' as const, label: tr('Gateway 用户面 WebSocket 基地址', 'Gateway user WebSocket base URL'), required: false },
  ];
  const flow = [
    [tr('连接已有服务', 'Connect existing services'), tr('确认部署命名空间和服务地址', 'Confirm the deployment namespace and endpoints')],
    [tr('集群准入', 'Cluster admission'), tr('允许哪些用户或组织进入', 'Choose who can enter')],
    [tr('运行资源', 'Runtime resources'), tr('绑定运行时模板并设置适用范围', 'Bind a runtime template and set its scope')],
    [tr('Agent 绑定', 'Agent bindings'), tr('选择模板并设置使用范围', 'Select templates and their access scope')],
    [tr('检查并验证', 'Check and verify'), tr('检查组件连接和实际对话', 'Check connectivity and an actual conversation')],
  ];
  const templateUnavailable = !!template && (templateState.loading || !!templateState.error || !templateState.data?.enabled);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (requestInFlight.current || templateUnavailable) return;
    if (!draft.name.trim()) { setError(tr('请填写集群名称。', 'Enter a cluster name.')); return; }
    const namespace = draft.namespace.trim();
    if (!namespace) { setError(tr('请填写已有服务实际部署的命名空间，可从部署命令的 -n 参数确认。', 'Enter the namespace used to deploy the existing services. Check the -n argument in the deployment command.')); return; }
    if (namespace.length > 63 || !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(namespace)) {
      setError(tr('命名空间只能包含小写字母、数字和连字符，长度不超过 63 个字符，不能以连字符开头或结尾。', 'Use up to 63 lowercase letters, digits and hyphens. The namespace cannot begin or end with a hyphen.'));
      return;
    }
    for (const field of endpointFields) {
      const value = hosts[field.key].trim();
      if (field.required && !value) { setError(t('instanceForm.fieldRequired', { field: field.label })); return; }
      if (value && !isValidHttpUrl(value)) { setError(t('instanceForm.hostInvalid', { field: field.label })); return; }
    }
    const unsafe = findUnsafeTextField([{ label: t('instanceForm.name'), value: draft.name }, { label: t('instanceForm.description'), value: draft.description }]);
    if (unsafe) { setError(t('instanceForm.unsafeText', { field: unsafe })); return; }
    requestInFlight.current = true;
    setSaving(true);
    setError('');
    try {
      const created = await InstanceApi.create({
        jiuwenclaw_name: draft.name.trim(), description: draft.description.trim() || undefined,
        namespace, space_id: 'default', created_by: user?.user_id || 'manager',
        gateway_host: hosts.gateway.trim(), runtime_host: hosts.runtime.trim(), user_web_host: hosts.web.trim(),
        gateway_web_http_host: hosts.gatewayHttp.trim(), gateway_web_ws_host: hosts.gatewayWs.trim() || undefined,
      });
      try { clearClusterCreationDraft(sessionStorage, draftKey); } catch { /* Creation has already succeeded. */ }
      bumpGuideRevision();
      toast('success', tr('服务实例已登记，继续配置集群准入。', 'Service instance registered. Continue with cluster admission.'));
      const context = new URLSearchParams();
      if (template) context.set('template', template);
      if (returnTo) context.set('returnTo', returnTo);
      if (mounted.current) navigate(guidedClusterWizardPath(created.jiuwenclaw_id, 2, context), { replace: true });
    } catch (cause) {
      setError(tr('创建集群失败：', 'Could not create cluster: ') + (cause instanceof ApiError ? cause.detail : (cause as Error).message));
    } finally {
      requestInFlight.current = false;
      setSaving(false);
    }
  };

  return <div className="wizard-workspace">
    <div className="page-header wizard-header">
      <div>
        <button type="button" className="btn ghost sm" disabled={saving} onClick={() => navigate('/instances')}>← {tr('集群管理', 'Clusters')}</button>
        <h1 className="page-title mt-3">{tr('新建集群', 'Create cluster')}</h1>
        <p className="page-subtitle">{tr('连接已经启动的服务，登记部署信息后继续配置集群准入和资源绑定。', 'Connect services that are already running, then configure cluster admission and resource bindings.')}</p>
      </div>
      <span className="tag">{tr('第 1 步 · 接入服务', 'Step 1 · Connect services')}</span>
    </div>
    {template && <section className="card mb-4 flex flex-wrap items-center justify-between gap-3">
      <div><strong>{tr('待绑定 Agent 模板', 'Agent template to bind')} · {templateState.data?.template_name || template}</strong><p className="mt-1 text-sm text-muted">{templateState.error || (templateState.loading ? tr('正在读取模板…', 'Loading template…') : !templateState.data?.enabled ? tr('模板已停用，请先在模板管理中启用。', 'Enable this template in Agent template management first.') : tr('此模板会保留到 Agent 绑定步骤，创建后仍按准入、运行资源、Agent 绑定的顺序继续。', 'The template will remain selected for the Agent binding step. Continue through admission and runtime resources first.'))}</p></div>
      <div className="flex gap-2"><button type="button" className="btn sm" disabled={saving} onClick={() => navigate(returnTo || '/agent-templates')}>{tr('返回模板管理', 'Return to templates')}</button>{templateState.error && <button type="button" className="btn sm" onClick={() => void templateState.reload()}>{tr('重试', 'Retry')}</button>}</div>
    </section>}
    <div className="wizard-layout">
      <aside className="card wizard-steps">
        <h2 className="text-sm font-semibold">{tr('新建集群流程', 'Cluster setup flow')}</h2>
        <ol className="mt-3 flex flex-col gap-1" aria-label={tr('集群配置流程', 'Cluster setup flow')}>
          {flow.map(([title, description], index) => <li key={title} className={'wizard-step ' + (index === 0 ? 'current' : '')} aria-current={index === 0 ? 'step' : undefined}><span className="wizard-step__num">{index + 1}</span><span><strong>{title}</strong><small>{description}</small></span></li>)}
        </ol>
        <p className="mt-4 text-xs leading-5 text-muted">{tr('创建成功后，每一步都在当前集群内完成。已保存的配置可随时从集群管理重新进入。', 'After creation, every step stays within the cluster. Saved settings can always be reopened from Clusters.')}</p>
      </aside>
      <main className="wizard-main">
        <WizardStepHeading step={tr('第 1 步 · 接入服务', 'Step 1 · Connect services')} title={tr('连接已有服务', 'Connect existing services')} description={tr('填写已启动服务的部署信息。保存后继续配置集群准入和资源绑定。', 'Enter the deployment details of your running services, then configure admission and resource bindings.')} actions={<span className="text-xs text-muted">{tr('草稿自动保留在当前标签页', 'Draft saved in this tab')}</span>} />
        <form className="card wizard-card" onSubmit={event => void create(event)}>
          <fieldset disabled={saving} className="m-0 min-w-0 border-0 p-0">
            <div className="wizard-form-grid">
              <label className="label" htmlFor="cluster-create-name">{tr('集群名称', 'Cluster name')} <span className="text-danger">*</span><input id="cluster-create-name" className="input" required maxLength={128} value={draft.name} onChange={event => update({ name: event.target.value })} placeholder={tr('例如：测试集群', 'e.g. Test cluster')} /><span className="wizard-field-help">{tr('用于在 Manager 中识别这套服务，可自行命名。', 'A display name to identify these services in Manager.')}</span></label>
              <label className="label" htmlFor="cluster-create-description">{tr('集群描述', 'Description')} <span className="text-muted">{tr('（可选）', '(optional)')}</span><input id="cluster-create-description" className="input" maxLength={4096} value={draft.description} onChange={event => update({ description: event.target.value })} placeholder={tr('例如：团队测试环境', 'e.g. Team test environment')} /></label>
            </div>
            <div className="wizard-section-heading"><strong>{tr('连接配置', 'Connection settings')}</strong><span>{tr('与已有服务的部署信息保持一致', 'Match your existing deployment')}</span></div>
            <div className="wizard-namespace">
              <label className="label" htmlFor="cluster-create-namespace">{tr('服务部署命名空间', 'Deployment namespace')} <span className="text-danger">*</span>
                <input id="cluster-create-namespace" className="input mono" required maxLength={63} value={draft.namespace} onChange={event => updateNamespace(event.target.value)} placeholder={tr('填写部署时使用的 namespace', 'Namespace used when deploying')} aria-describedby="cluster-namespace-help" autoCapitalize="none" autoCorrect="off" spellCheck={false} />
              </label>
              <p id="cluster-namespace-help" className="wizard-field-help">{tr('填写服务启动时实际使用的命名空间，即部署命令中 -n 后的值。只有部署在 default 时才填写 default。', 'Use the actual namespace from the -n argument of your deployment command. Enter default only if your services run there.')}</p>
              {deployedNamespace && <div className="wizard-namespace-suggestion"><span>{tr('当前 Manager 的部署配置使用', 'This Manager deployment uses')} <code>{deployedNamespace}</code>{tr('，连接这套服务时可直接填入。', '. Use it when connecting these services.')}</span><button type="button" className="btn sm" onClick={() => updateNamespace(deployedNamespace)}>{tr('填入', 'Use')} {deployedNamespace}</button></div>}
              <details className="wizard-namespace-help"><summary>{tr('在哪里查看命名空间？', 'Where can I find the namespace?')}</summary><p>{tr('例如，使用以下命令启动服务时，这里应填写 team-a：', 'For example, services started with this command use team-a:')}</p><code>./deploy.sh up -n team-a</code><p>{tr('也可在服务器执行 kubectl get svc -A，查看 jiuwenswarm-gateway、jiuwenswarm-agent-runtime 等服务所在行的 NAMESPACE 列。', 'You can also run kubectl get svc -A on the server and check the NAMESPACE column for jiuwenswarm-gateway and jiuwenswarm-agent-runtime.')}</p></details>
            </div>
            <div className="wizard-endpoints-heading"><div><h3>{tr('服务地址', 'Service endpoints')}</h3><p className="wizard-field-help">{tr('按上方命名空间预填 Kubernetes 内部地址，可直接修改。跨集群或使用自定义服务名时，请填写 Manager 服务端可访问的地址。', 'Kubernetes URLs are filled from the namespace above and can be edited directly. For another cluster or custom service names, use URLs reachable from the Manager server.')}</p></div><button type="button" className="btn ghost sm" disabled={!draft.namespace.trim()} onClick={() => update({ ...clusterCreationHosts(draft.namespace), hostMode: 'manual' })}>{tr('重置为标准地址', 'Reset standard URLs')}</button></div>
            <div className="wizard-endpoint-fields">
              {endpointFields.slice(0, 3).map(field => <label className="label wizard-endpoint-field" key={field.key} htmlFor={'cluster-create-' + field.key}><span>{field.label} <span className="text-danger">*</span></span><input id={'cluster-create-' + field.key} className="input mono" required value={hosts[field.key]} maxLength={512} onChange={event => updateEndpoint(field.key, event.target.value)} placeholder="http://host:port" autoCapitalize="none" spellCheck={false} /></label>)}
            </div>
            <details className="wizard-extra-endpoints" open={hosts.gatewayHttp !== hosts.gateway || hosts.gatewayWs !== hosts.gateway ? true : undefined}>
              <summary>{tr('更多连接地址', 'Additional endpoints')} <span>{tr('Gateway 用户面 HTTP / WebSocket', 'Gateway user HTTP / WebSocket')}</span></summary>
              <p className="wizard-field-help">{tr('标准部署使用相同的 Gateway 基地址；需要独立访问地址时可在此修改。WebSocket 请填写 HTTP(S) 基地址，留空时由服务使用默认值。', 'Standard deployments use the same Gateway base URL. Edit these for separate endpoints. Use an HTTP(S) base URL for WebSocket, or leave it blank for the service default.')}</p>
              <div className="wizard-endpoint-fields">{endpointFields.slice(3).map(field => <label className="label wizard-endpoint-field" key={field.key} htmlFor={'cluster-create-' + field.key}><span>{field.label}{field.required && <span className="text-danger"> *</span>}</span><input id={'cluster-create-' + field.key} className="input mono" value={hosts[field.key]} maxLength={512} onChange={event => updateEndpoint(field.key, event.target.value)} placeholder="http://host:port" autoCapitalize="none" spellCheck={false} /></label>)}</div>
            </details>
          </fieldset>
          {error && <p className="mt-4 text-sm text-danger break-words" role="alert">{error}</p>}
          <div className="wizard-actions"><button type="button" className="btn" disabled={saving} onClick={() => navigate('/instances')}>{tr('暂时离开', 'Leave for now')}</button><button type="submit" className="btn primary" disabled={saving || templateUnavailable}>{saving ? tr('保存中…', 'Saving…') : tr('保存并配置集群准入', 'Save and configure admission')} →</button></div>
        </form>
      </main>
    </div>
  </div>;
}
