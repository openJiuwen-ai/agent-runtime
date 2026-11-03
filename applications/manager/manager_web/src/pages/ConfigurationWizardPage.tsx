import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { AgentTemplateApi, ApiError, InstanceApi } from '../services/api';
import { WorkspaceNote } from '../components/WorkspaceNote';
import { toast } from '../stores/uiStore';
import { bumpGuideRevision } from '../stores/guideStore';
import { templateReturnPath } from '../utils/agentFlowContext';
import { clusterSetupPath, legacyClusterWizardPath } from '../utils/clusterWizard';
import {
  clearClusterCreationDraft, clusterCreationDraftKey, clusterCreationHosts,
  emptyClusterCreationDraft, readClusterCreationDraft, saveClusterCreationDraft,
  type ClusterCreationDraft,
} from '../utils/clusterCreationDraft';
import { isValidHttpUrl } from '../utils/url';
import { findUnsafeTextField } from '../utils/safeText';

/** New clusters are created once, then configured on their actual cluster pages. */
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
  const templateState = useAsync(() => template ? AgentTemplateApi.get(template) : Promise.resolve(null), [template]);
  const autoHosts = useMemo(() => clusterCreationHosts(draft.namespace), [draft.namespace]);
  const update = (patch: Partial<ClusterCreationDraft>) => { setError(''); setDraft(value => ({ ...value, ...patch })); };

  useEffect(() => {
    try { saveClusterCreationDraft(sessionStorage, draftKey, draft); } catch { /* Storage availability does not affect Manager CRUD. */ }
  }, [draft, draftKey]);

  const endpointFields = [
    { key: 'gateway' as const, label: t('instanceForm.gatewayHost'), required: true },
    { key: 'runtime' as const, label: t('instanceForm.runtimeHost'), required: true },
    { key: 'web' as const, label: t('instanceForm.userWebHost'), required: true },
    { key: 'gatewayHttp' as const, label: t('instanceForm.gatewayWebHttpHost'), required: true },
    { key: 'gatewayWs' as const, label: t('instanceForm.gatewayWebWsHost'), required: false },
  ];
  const flow = [
    [tr('创建服务实例', 'Create service instance'), tr('保存集群名称和服务地址', 'Save the cluster identity and endpoints')],
    [tr('集群准入', 'Cluster admission'), tr('允许哪些用户或组织进入', 'Choose who can enter')],
    [tr('运行资源', 'Runtime resources'), tr('启用运行规格并设置适用范围', 'Enable runtime profiles and their scope')],
    [tr('Agent 绑定', 'Agent bindings'), tr('选择模板并设置使用范围', 'Select templates and their access scope')],
    [tr('检查并验证', 'Check and verify'), tr('检查组件连接和实际对话', 'Check connectivity and an actual conversation')],
  ];
  const templateUnavailable = !!template && (templateState.loading || !!templateState.error || !templateState.data?.enabled);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (requestInFlight.current || templateUnavailable) return;
    if (!draft.name.trim()) { setError(tr('请填写集群名称。', 'Enter a cluster name.')); return; }
    const namespace = draft.namespace.trim() || 'default';
    if (namespace.length > 63 || !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(namespace)) {
      setError(tr('命名空间只能包含小写字母、数字和连字符，长度不超过 63 个字符，不能以连字符开头或结尾。', 'Use up to 63 lowercase letters, digits and hyphens. The namespace cannot begin or end with a hyphen.'));
      return;
    }
    const hosts = draft.hostMode === 'auto' ? autoHosts : draft;
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
      toast('success', tr('服务实例已创建，继续配置集群准入。', 'Service instance created. Continue with cluster admission.'));
      const context = new URLSearchParams();
      if (template) context.set('template', template);
      if (returnTo) context.set('returnTo', returnTo);
      navigate(clusterSetupPath(created.jiuwenclaw_id, 'access', context), { replace: true });
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
        <p className="page-subtitle">{tr('先保存服务实例，之后在该集群中完成准入、运行资源、Agent 绑定和检查。', 'Save the service instance, then configure admission, runtime resources, Agent bindings and checks inside that cluster.')}</p>
      </div>
      <span className="tag">{tr('第 1 步 · 创建服务实例', 'Step 1 · Create service instance')}</span>
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
        <form className="card wizard-card" onSubmit={event => void create(event)}>
          <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="font-semibold">{tr('创建服务实例', 'Create service instance')}</h2><p className="wizard-help">{tr('登记集群标识和实际服务地址，后续配置将绑定到这个服务实例。', 'Register the cluster identity and service endpoints. Subsequent settings belong to this service instance.')}</p></div><span className="text-xs text-muted">{tr('离开后可在本标签页恢复草稿', 'Draft resumes in this browser tab')}</span></div>
          <fieldset disabled={saving} className="m-0 min-w-0 border-0 p-0">
            <div className="wizard-form-grid">
              <label className="label" htmlFor="cluster-create-name">{tr('集群名称', 'Cluster name')} <span className="text-danger">*</span><input id="cluster-create-name" className="input" maxLength={128} value={draft.name} onChange={event => update({ name: event.target.value })} placeholder={tr('例如：测试集群', 'e.g. Test cluster')} /></label>
              <label className="label" htmlFor="cluster-create-namespace">{tr('命名空间', 'Namespace')}<input id="cluster-create-namespace" className="input" maxLength={63} value={draft.namespace} onChange={event => update({ namespace: event.target.value })} placeholder="default" /></label>
              <label className="label wizard-form-wide" htmlFor="cluster-create-description">{tr('集群描述', 'Description')}<textarea id="cluster-create-description" className="input" rows={2} maxLength={4096} value={draft.description} onChange={event => update({ description: event.target.value })} /></label>
            </div>
            <div className="wizard-section-heading"><strong>{tr('服务地址', 'Service endpoints')}</strong><span>{tr('填写 Manager 可以访问的实际地址。', 'Use endpoints reachable from Manager.')}</span></div>
            <div className="tabs-bar wizard-tabs" role="tablist" aria-label={tr('服务地址填写方式', 'Endpoint entry mode')}>
              <button type="button" role="tab" aria-selected={draft.hostMode === 'auto'} className={'tab ' + (draft.hostMode === 'auto' ? 'active' : '')} onClick={() => update({ hostMode: 'auto' })}>{tr('自动生成', 'Auto generate')}</button>
              <button type="button" role="tab" aria-selected={draft.hostMode === 'manual'} className={'tab ' + (draft.hostMode === 'manual' ? 'active' : '')} onClick={() => update({ ...(endpointFields.every(field => !draft[field.key]) ? autoHosts : {}), hostMode: 'manual' })}>{tr('手动填写', 'Manual')}</button>
            </div>
            <p className="mt-2 text-xs text-muted">{draft.hostMode === 'auto' ? tr('按 Kubernetes 服务名和命名空间生成；跨网络访问时请切换为手动填写。', 'Generated from Kubernetes services and namespace. Use manual entry when accessing across networks.') : tr('Gateway WebSocket 填写 HTTP(S) 基地址，未填写时由服务使用默认值。', 'Enter the HTTP(S) base URL for Gateway WebSocket. Leave it blank to use the service default.')}</p>
            {draft.hostMode === 'auto' ? <div className="wizard-endpoint-preview">{endpointFields.map(field => <div key={field.key}><span>{field.label}</span><code>{autoHosts[field.key]}</code></div>)}</div> : <div className="wizard-form-grid">{endpointFields.map(field => <label className="label" key={field.key} htmlFor={'cluster-create-' + field.key}>{field.label}{field.required && <span className="text-danger"> *</span>}<input id={'cluster-create-' + field.key} className="input mono" value={draft[field.key]} maxLength={512} onChange={event => update({ [field.key]: event.target.value })} placeholder="http://host:port" /></label>)}</div>}
          </fieldset>
          {error && <p className="mt-4 text-sm text-danger break-words" role="alert">{error}</p>}
          <div className="wizard-actions"><button type="button" className="btn" disabled={saving} onClick={() => navigate('/instances')}>{tr('暂时离开', 'Leave for now')}</button><button type="submit" className="btn primary" disabled={saving || templateUnavailable}>{saving ? tr('创建中…', 'Creating…') : tr('创建并配置集群准入', 'Create and configure admission')} →</button></div>
        </form>
        <WorkspaceNote title={tr('新建集群只登记一个服务实例', 'Creating a cluster registers one service instance')}>{tr('本页保存集群名称和连接地址，不会自动创建运行资源或授予 Agent 使用权限。创建后按集群准入 → 运行资源 → Agent 绑定 → 检查并验证继续；完成保存的配置会保留在该集群中。', 'This page saves the cluster identity and endpoints. Continue with admission, runtime resources, Agent bindings and verification. Each saved configuration remains attached to the cluster.')}</WorkspaceNote>
      </main>
    </div>
  </div>;
}
