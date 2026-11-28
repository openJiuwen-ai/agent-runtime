import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { useAsync } from '../../hooks/useAsync';
import { EditorPresentationProvider, InlineEditorProvider } from '../../components/InlineEditorContext';
import { MatchExprEditor } from '../../components/MatchExprEditor';
import { AgentTemplateModal } from '../templates/AgentTemplateModal';
import { ServiceConfigTemplateModal } from '../templates/ServiceConfigTemplateModal';
import {
  AgentDefinitionApi, AgentTemplateApi, InstanceAgentResourceApi, InstanceServiceResourceApi,
  ServiceConfigTemplateApi, type AgentTemplate, type InstanceAgentResource, type InstanceServiceResource,
} from '../../services/api';
import type { ServiceConfigTemplate } from '../../types';
import { collectPages } from '../../utils/overview';
import { activeRuntimeTemplateIds, ensureWizardAgentDefinition, initialWizardResource, matchingAttemptResource, runtimePriorityConflict } from '../../utils/wizardResources';
import { agentResourceUpdateBody, resourceExpiryForUpdate, serviceResourceUpdateBody } from '../../utils/instanceResourceUpdate';
import { AGENT_RESOURCE_MATCH_FIELDS, matchExprToEditorString, parseMatchExpr, validateMatchExprModel } from '../../utils/matchExpr';
import { bumpGuideRevision } from '../../stores/guideStore';
import './WizardResources.css';
import { WizardStepHeading } from './WizardStepHeading';

type Resource = InstanceAgentResource | InstanceServiceResource;
type Template = AgentTemplate | ServiceConfigTemplate;
interface Draft {
  resourceId: string; templateId: string; name: string; description: string; scope: string;
  priority: number; enabled: boolean; expiresAt: string; runtimeId: string;
}
export interface WizardResourceProps {
  instanceId: string;
  initialTemplateId?: string;
  active?: boolean;
  revision?: number;
  onSaved: () => void;
  onBusyChange?: (busy: boolean) => void;
}
const emptyDraft = (): Draft => ({ resourceId: '', templateId: '', name: '', description: '', scope: '', priority: 0, enabled: true, expiresAt: '', runtimeId: '' });
function expiryInput(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
}
function resourceDraft(row: Resource): Draft {
  return { ...emptyDraft(), resourceId: row.resource_id, templateId: row.ref_template_id, name: row.resource_name ?? '', description: row.resource_desc ?? '', scope: matchExprToEditorString(row.match_expr), priority: 'priority' in row ? row.priority : 0, enabled: row.enabled, expiresAt: expiryInput(row.expires_at) };
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }

/** Both resource steps keep their own draft mounted while full template editors run inline. */
export function WizardResourceBinding({ kind, instanceId, initialTemplateId, active = true, revision = 0, onSaved, onBusyChange }: WizardResourceProps & { kind: 'runtime' | 'agent' }) {
  const { i18n } = useTranslation();
  const { user } = useAuth();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const isRuntime = kind === 'runtime';
  const [templates, setTemplates] = useState<Template[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [runtimeTemplates, setRuntimeTemplates] = useState<ServiceConfigTemplate[]>([]);
  const [runtimeResources, setRuntimeResources] = useState<InstanceServiceResource[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [baseline, setBaseline] = useState('');
  const [initialized, setInitialized] = useState(false);
  const [definitionPending, setDefinitionPending] = useState(false);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState<{ copy?: Template } | null>(null);
  const [details, setDetails] = useState<Template | null>(null);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const attemptStarted = useRef(false);
  const requestPending = useRef(false);
  const handoffKey = initialTemplateId ? `:${encodeURIComponent(initialTemplateId)}` : '';
  const failedAttemptKey = `manager:wizard-resource-attempt:${user?.user_id ?? ''}:${instanceId}:${kind}${handoffKey}`;
  const storageKey = `manager:wizard-resource:${user?.user_id ?? ''}:${instanceId}:${kind}${handoffKey}`;
  const initializedKey = useRef('');
  const draftChanged = initialized && JSON.stringify(draft) !== baseline;
  const dirty = draftChanged || definitionPending;
  const update = (changes: Partial<Draft>) => setDraft(current => ({ ...current, ...changes }));
  const activeRuntimeIds = activeRuntimeTemplateIds(runtimeResources, runtimeTemplates.filter(item => item.enabled).map(item => item.template_id));
  const selected = templates.find(item => item.template_id === draft.templateId);
  const currentResource = resources.find(item => item.resource_id === draft.resourceId);
  const runtimeId = activeRuntimeIds.includes(draft.runtimeId) ? draft.runtimeId : activeRuntimeIds[0] || '';

  const state = useAsync(async () => {
    if (!active && initialized) return null;
    if (isRuntime) {
      const [catalog, rows] = await Promise.all([
        collectPages((page, page_size) => ServiceConfigTemplateApi.list({ page, page_size })),
        collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size })),
      ]);
      return { catalog: catalog as Template[], rows: rows as Resource[], runtimeCatalog: catalog, runtimeRows: rows };
    }
    const [catalog, rows, runtimeCatalog, runtimeRows] = await Promise.all([
      collectPages((page, page_size) => AgentTemplateApi.list({ page, page_size })),
      collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size })),
      collectPages((page, page_size) => ServiceConfigTemplateApi.list({ page, page_size })),
      collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size })),
    ]);
    return { catalog: catalog as Template[], rows: rows as Resource[], runtimeCatalog, runtimeRows };
  }, [instanceId, kind, revision, active]);

  useEffect(() => {
    if (!state.data) return;
    setTemplates(state.data.catalog); setResources(state.data.rows);
    setRuntimeTemplates(state.data.runtimeCatalog); setRuntimeResources(state.data.runtimeRows);
    setCatalogRevision(value => value + 1);
    if (initialized && initializedKey.current === storageKey) return;
    const first = initialWizardResource(state.data.rows, initialTemplateId);
    const next = first ? resourceDraft(first) : emptyDraft();
    if (!first) {
      const preferred = initialTemplateId
        ? state.data.catalog.find(row => row.template_id === initialTemplateId)
        : state.data.catalog.find(row => row.enabled && Boolean(row.data?.deployment_default));
      if (preferred) { next.templateId = preferred.template_id; next.name = preferred.template_name; }
      const priorities = state.data.rows.filter(row => row.enabled && 'priority' in row).map(row => (row as InstanceServiceResource).priority);
      next.priority = priorities.length ? Math.max(...priorities) + 1 : 0;
    }
    next.runtimeId = activeRuntimeTemplateIds(state.data.runtimeRows, state.data.runtimeCatalog.filter(row => row.enabled).map(row => row.template_id))[0] ?? '';
    setBaseline(JSON.stringify(next));
    try {
      const raw = window.sessionStorage.getItem(storageKey);
      const restored: unknown = raw ? JSON.parse(raw) : null;
      if (restored && typeof restored === 'object' && Object.entries(next).every(([key, value]) => typeof (restored as Record<string, unknown>)[key] === typeof value)) {
        setDraft(Object.fromEntries(Object.keys(next).map(key => [key, (restored as Record<string, unknown>)[key]])) as unknown as Draft);
      } else setDraft(next);
    } catch { setDraft(next); }
    setInitialized(true);
    initializedKey.current = storageKey;
  }, [state.data, initialized, initialTemplateId, storageKey]);

  useEffect(() => {
    if (!initialized) return;
    try {
      if (dirty) window.sessionStorage.setItem(storageKey, JSON.stringify(draft));
      else window.sessionStorage.removeItem(storageKey);
    } catch { /* In-memory draft remains usable if storage is unavailable. */ }
  }, [draft, dirty, initialized, storageKey]);
  const runtimeChoiceKey = activeRuntimeIds.join('|');
  useEffect(() => {
    if (!initialized || isRuntime || activeRuntimeIds.includes(draft.runtimeId) || !activeRuntimeIds.length) return;
    const next = { ...draft, runtimeId: activeRuntimeIds[0] };
    setDraft(next);
    if (!dirty) setBaseline(JSON.stringify(next));
    // Reuse an active runtime from step 3, including after a stored choice becomes unavailable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialized, isRuntime, runtimeChoiceKey, draft.runtimeId]);
  useEffect(() => { onBusyChange?.(dirty || definitionPending || Boolean(editor) || saving); }, [dirty, definitionPending, editor, saving, onBusyChange]);
  useEffect(() => () => { onBusyChange?.(false); }, [onBusyChange]);

  function chooseTemplate(id: string) {
    const item = templates.find(row => row.template_id === id);
    update({ templateId: id, name: draft.name || item?.template_name || '' });
  }
  async function inspectTemplate(item: Template) {
    setError('');
    try { setDetails(isRuntime ? await ServiceConfigTemplateApi.get(item.template_id) : await AgentTemplateApi.get(item.template_id)); }
    catch (cause) { setError(errorMessage(cause)); }
  }
  async function copyTemplate(item: Template) {
    setError('');
    try { setEditor({ copy: isRuntime ? await ServiceConfigTemplateApi.get(item.template_id) : await AgentTemplateApi.get(item.template_id) }); setDetails(null); }
    catch (cause) { setError(errorMessage(cause)); }
  }
  function templateSaved(item: Template, keepOpen = false) {
    setTemplates(current => [item, ...current.filter(row => row.template_id !== item.template_id)]);
    update({ templateId: item.template_id, name: draft.name || item.template_name });
    if (!keepOpen) setEditor(null);
    setDetails(null); setQuery('');
  }

  async function save() {
    if (requestPending.current) return;
    setError('');
    if (!selected?.enabled) { setError(tr('请选择一个已启用的模板。', 'Choose an enabled template.')); return; }
    if (!draft.name.trim()) { setError(isRuntime ? tr('请填写绑定名称。', 'Enter a binding name.') : tr('请填写 Agent 名称。', 'Enter an Agent name.')); return; }
    const scopeError = validateMatchExprModel(parseMatchExpr(draft.scope), isRuntime ? undefined : AGENT_RESOURCE_MATCH_FIELDS);
    if (scopeError) { setError(tr('请补全适用范围中的条件。', 'Complete the scope conditions.')); return; }
    if (isRuntime && (!Number.isInteger(draft.priority) || draft.priority < -2147483648 || draft.priority > 2147483647)) {
      setError(tr('优先级必须是有效整数。', 'Priority must be a valid integer.')); return;
    }
    if (!isRuntime && !runtimeId) {
      setError(tr('请先在第 3 步保存有效的运行资源，再继续配置 Agent。', 'Save an active runtime resource in step 3 before configuring an Agent.')); return;
    }
    const unchangedExpiry = !!currentResource && draft.expiresAt === expiryInput(currentResource.expires_at);
    if (draft.expiresAt && !unchangedExpiry && (!Number.isFinite(Date.parse(draft.expiresAt)) || Date.parse(draft.expiresAt) <= Date.now())) {
      setError(tr('过期时间必须晚于当前时间。', 'Expiry must be in the future.')); return;
    }
    requestPending.current = true;
    setSaving(true);
    let savedRow: Resource | undefined = currentResource;
    let bindingSaved = false;
    try {
      const liveRows: Resource[] = isRuntime
        ? await collectPages((page, page_size) => InstanceServiceResourceApi.listInstanceResources(instanceId, { page, page_size }))
        : await collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { page, page_size }));
      if (draft.resourceId) {
        savedRow = liveRows.find(row => row.resource_id === draft.resourceId);
        if (!savedRow) throw new Error(tr('此绑定已被删除，请刷新后重新选择。', 'This binding was deleted. Reload and choose again.'));
      }
      const expiresAt = resourceExpiryForUpdate(savedRow?.expires_at, draft.expiresAt);
      const attempt = { templateId: draft.templateId, name: draft.name.trim(), scope: draft.scope, enabled: draft.enabled, expiresAt, ...(isRuntime ? { priority: draft.priority } : {}) };
      try { if (window.sessionStorage.getItem(failedAttemptKey) === JSON.stringify(attempt)) attemptStarted.current = true; } catch { /* noop */ }
      if (!savedRow && attemptStarted.current) savedRow = matchingAttemptResource(liveRows, attempt);
      if (isRuntime && draft.enabled && runtimePriorityConflict(liveRows as InstanceServiceResource[], draft.priority, savedRow?.resource_id)) {
        throw new Error(tr('该优先级已被其他启用的运行时绑定使用，请选择不同的整数。', 'Another enabled runtime binding uses this priority. Choose a different integer.'));
      }
      const common = { ref_template_id: draft.templateId, resource_name: draft.name.trim(), resource_desc: draft.description.trim() || null, match_exprs: [draft.scope], enabled: draft.enabled, expires_at: expiresAt };
      attemptStarted.current = true;
      try { window.sessionStorage.setItem(failedAttemptKey, JSON.stringify(attempt)); } catch { /* noop */ }
      const unchanged = savedRow && savedRow.ref_template_id === common.ref_template_id
        && savedRow.resource_name === common.resource_name && (savedRow.resource_desc ?? null) === common.resource_desc
        && savedRow.enabled === common.enabled && (savedRow.expires_at ?? null) === common.expires_at
        && matchExprToEditorString(savedRow.match_expr) === draft.scope
        && (!isRuntime || ('priority' in savedRow && savedRow.priority === draft.priority));
      if (!unchanged) {
        const preservingScope = savedRow && matchExprToEditorString(savedRow.match_expr) === draft.scope
          ? { ...common, match_exprs: [savedRow.match_expr] } : common;
        const response = isRuntime
          ? savedRow
            ? await InstanceServiceResourceApi.update(instanceId, savedRow.resource_id, serviceResourceUpdateBody(savedRow as InstanceServiceResource, { ...preservingScope, priority: draft.priority }))
            : await InstanceServiceResourceApi.create(instanceId, { ...common, priority: draft.priority })
          : savedRow
            ? await InstanceAgentResourceApi.update(instanceId, savedRow.resource_id, agentResourceUpdateBody(savedRow as InstanceAgentResource, preservingScope))
            : await InstanceAgentResourceApi.create(instanceId, common);
        savedRow = response.items[0];
      }
      if (!savedRow) throw new Error(tr('服务端未返回保存结果，请刷新确认后再继续。', 'The server returned no saved binding. Reload to confirm before continuing.'));
      bindingSaved = true;
      const savedDraft = { ...draft, resourceId: savedRow.resource_id };
      setResources(current => [...current.filter(row => row.resource_id !== savedRow!.resource_id), savedRow!]);
      setDraft(savedDraft); setBaseline(JSON.stringify(savedDraft));
      try { window.sessionStorage.removeItem(storageKey); window.sessionStorage.removeItem(failedAttemptKey); } catch { /* noop */ }
      if (!isRuntime) {
        setDefinitionPending(true);
        await ensureWizardAgentDefinition({
          list: () => collectPages((page, page_size) => AgentDefinitionApi.list({ page, page_size })),
          create: () => AgentDefinitionApi.create({ agent_config_template_id: draft.templateId, runtime_template_id: runtimeId, agent_name: draft.name.trim() }),
        }, draft.templateId, runtimeId);
        setDefinitionPending(false);
      }
      bumpGuideRevision();
      onSaved();
    } catch (cause) {
      setError(`${!isRuntime && bindingSaved ? tr('绑定已保存；Agent 定义尚未完成，可直接重试。', 'The binding was saved; the Agent definition is incomplete. Retry safely.') + ' ' : ''}${errorMessage(cause)}`);
    } finally { requestPending.current = false; setSaving(false); }
  }

  const filtered = templates.filter(item => `${item.template_name} ${item.template_id}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="wizard-resource-step">
    <div hidden={Boolean(editor || details)}>
      <WizardStepHeading step={isRuntime ? tr('第 3 步 · 运行资源', 'Step 3 · Runtime resources') : tr('第 4 步 · Agent', 'Step 4 · Agent')} title={isRuntime ? tr('选择 Agent 的运行环境', 'Choose where Agents run') : tr('定义可用的 Agent', 'Configure an available Agent')} description={isRuntime ? tr('复用已有运行时模板即可。模板已包含容器配置，仅在需要定制时复制或新建。', 'Reuse a runtime template, including its container configuration. Copy or create a template only when customization is needed.') : tr('选择 Agent 配置模板，设置使用范围，并与本集群运行时组成用户面可选择的 Agent。', 'Choose an Agent configuration template, set its scope, and pair it with this cluster’s runtime for the user console.')} />
      {state.loading && !initialized ? <div className="card wizard-resource-empty" role="status">{tr('正在读取模板与绑定…', 'Loading templates and bindings…')}</div> : state.error ? <div className="card wizard-resource-empty" role="alert"><p>{state.error}</p><button className="btn" onClick={() => void state.reload()}>{tr('重新读取', 'Reload')}</button></div> : <>
        <section className="card wizard-resource-form"><div className="wizard-resource-section-heading wizard-resource-catalog-heading"><div><h3>{isRuntime ? tr('运行时模板', 'Runtime template') : tr('Agent 配置模板', 'Agent configuration template')}</h3></div><div className="wizard-resource-catalog-actions"><input className="input" aria-label={tr('搜索模板', 'Search templates')} placeholder={tr('搜索名称或 ID', 'Search name or ID')} value={query} onChange={event => setQuery(event.target.value)} /><button type="button" className="btn" disabled={saving} onClick={() => setEditor({})}>＋ {tr('新建模板', 'New template')}</button></div></div>
          <div className="wizard-resource-template-list" role="radiogroup" aria-label={tr('选择模板', 'Select a template')}>
            {filtered.map(item => <div key={item.template_id} className={`wizard-resource-template ${draft.templateId === item.template_id ? 'is-selected' : ''}`}><label><input type="radio" name={`wizard-${kind}-${instanceId}`} checked={draft.templateId === item.template_id} disabled={saving || !item.enabled} onChange={() => chooseTemplate(item.template_id)} /><span><strong>{item.template_name}</strong>{Boolean(item.data?.deployment_default) && <span className="tag">{tr('默认', 'Default')}</span>}{!item.enabled && <span className="tag">{tr('已停用', 'Disabled')}</span>}<small>{item.description || item.template_id}</small></span></label><div><button type="button" className="btn sm" disabled={saving} onClick={() => void inspectTemplate(item)}>{tr('详情', 'Details')}</button><button type="button" className="btn sm" disabled={saving} onClick={() => void copyTemplate(item)}>{tr('复制并自定义', 'Copy and customize')}</button></div></div>)}
            {!filtered.length && <div className="wizard-resource-empty">{templates.length ? tr('没有匹配的模板。', 'No templates match your search.') : tr('暂无模板，请新建并读取默认配置，或自行填写。', 'No templates yet. Create one and load deployment defaults, or enter your configuration.')}</div>}
          </div>

          <div className="wizard-resource-section-heading wizard-resource-divider"><div><h3>{tr('在本集群中的使用方式', 'Usage in this cluster')}</h3></div></div>
          <div className="wizard-resource-fields"><label><span>{isRuntime ? tr('绑定名称', 'Binding name') : tr('Agent名称', 'Agent name')} *</span><input className="input" maxLength={128} value={draft.name} disabled={saving} onChange={event => update({ name: event.target.value })} /></label>{isRuntime && <label><span>{tr('优先级', 'Priority')} *</span><input className="input" type="number" step="1" value={draft.priority} disabled={saving} onChange={event => update({ priority: Number(event.target.value) })} /><small>{tr('数值越小越优先；同一集群的启用绑定不可重复。', 'Lower values take precedence; enabled bindings need unique priorities.')}</small></label>}<label className="wizard-resource-field-wide"><span>{tr('描述', 'Description')}</span><textarea className="input" rows={2} maxLength={512} value={draft.description} disabled={saving} onChange={event => update({ description: event.target.value })} /></label></div>
          <fieldset disabled={saving} className="wizard-resource-scope"><legend>{tr('适用范围', 'Scope')}</legend><p>{tr('全部表示所有已获准进入集群的身份；也可按用户或组织进一步限制。', 'All includes identities admitted to this cluster. You can further restrict by user or organization.')}</p><MatchExprEditor revision={catalogRevision} value={draft.scope} onChange={scope => update({ scope })} allowedFields={isRuntime ? undefined : AGENT_RESOURCE_MATCH_FIELDS} /></fieldset>
          <details className="wizard-resource-advanced"><summary>{tr('有效期与启用状态', 'Expiry and enabled status')}</summary><div className="wizard-resource-fields"><label><span>{tr('过期时间（留空永久有效）', 'Expiry (blank means no expiry)')}</span><input className="input" type="datetime-local" value={draft.expiresAt} disabled={saving} onChange={event => update({ expiresAt: event.target.value })} /></label><label className="wizard-resource-checkbox"><input type="checkbox" checked={draft.enabled} disabled={saving} onChange={event => update({ enabled: event.target.checked })} /><span>{tr('启用此绑定', 'Enable this binding')}</span></label></div></details>
        </section>
        {!isRuntime && initialized && !state.error && !activeRuntimeIds.length && <p role="status" className="wizard-resource-error">{tr('请先在第 3 步保存有效的运行资源，再继续配置 Agent。', 'Save an active runtime resource in step 3 before configuring an Agent.')}</p>}
        {error && <div className="wizard-resource-error" role="alert">{error}</div>}
        <div className="wizard-resource-actions"><span>{tr('模板保存后会保留在全局目录；此步骤保存后才创建集群绑定。', 'Saved templates remain in the global directory. Cluster bindings are saved in this step.')}</span><button type="button" className="btn primary" disabled={saving || !initialized || !selected?.enabled || (!isRuntime && !runtimeId)} onClick={() => void save()}>{saving ? tr('正在保存…', 'Saving…') : isRuntime ? tr('保存并配置 Agent →', 'Save and configure Agent →') : tr('保存并验证 →', 'Save and verify →')}</button></div>
      </>}
    </div>
    {editor && <InlineEditorProvider active={active}>{isRuntime ? <ServiceConfigTemplateModal open template={null} copyFrom={editor.copy as ServiceConfigTemplate | undefined} onClose={() => setEditor(null)} onSaved={item => templateSaved(item)} /> : <AgentTemplateModal open template={null} copyFrom={editor.copy as AgentTemplate | undefined} onClose={() => setEditor(null)} onSaved={(item, options) => templateSaved(item, Boolean(options?.keepOpen))} />}</InlineEditorProvider>}
    {details && <InlineEditorProvider active={active}><EditorPresentationProvider readOnly footer={<><button type="button" className="btn" onClick={() => setDetails(null)}>{tr('返回绑定配置', 'Back to binding')}</button><button type="button" className="btn primary" onClick={() => void copyTemplate(details)}>{tr('复制并自定义', 'Copy and customize')}</button></>}>{isRuntime ? <ServiceConfigTemplateModal open template={details as ServiceConfigTemplate} onClose={() => setDetails(null)} onSaved={() => {}} /> : <AgentTemplateModal open template={details as AgentTemplate} onClose={() => setDetails(null)} onSaved={() => {}} />}</EditorPresentationProvider></InlineEditorProvider>}
  </div>;
}
