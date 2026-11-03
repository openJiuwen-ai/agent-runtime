import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { ConfigRefreshCostHint } from '../../../components/ConfigRefreshCostHint';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { useAsync } from '../../../hooks/useAsync';
import { useFormDirty } from '../../../hooks/useFormDirty';
import {
  InstanceAgentResourceApi,
  AgentTemplateApi,
  ApiError,
  InstanceAgentResource,
} from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import { bumpGuideRevision } from '../../../stores/guideStore';
import {
  matchExprToEditorString,
  parseMatchExpr,
  validateMatchExprModel,
  AGENT_RESOURCE_MATCH_FIELDS,
} from '../../../utils/matchExpr';
import { formatTime } from '../../../utils/format';
import { Empty } from '../../../components/Empty';
import { Pagination } from '../../../components/Pagination';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Switch } from '../../../components/Switch';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { useListSearch } from '../../../hooks/useListSearch';
import { useGuideAutoOpen } from '../../../hooks/useGuideAutoOpen';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';
import { GuideLink } from '../../../components/GuideLink';
import { useRouter } from '../../../router';
import { collectPages } from '../../../utils/overview';
import { useAuth } from '../../../auth/AuthContext';
import { applyAgentTemplateChoice, clusterBindingDraftKey, emptyClusterBindingDraft, readClusterBindingDraft, readClusterBindingHandoff, writeClusterBindingDraft, type ClusterBindingDraft } from '../../../utils/agentDraft';
import { AgentExpertPreviewSummary } from '../../../components/AgentExpertBindingsPreview';
import { agentPreparationCatalogPath, consumeAgentBindingAction } from '../../../utils/agentFlowContext';

interface Props {
  instanceId: string;
}

type AgentInstanceSortField =
  | 'resource_id'
  | 'template_name'
  | 'granted_by'
  | 'expires_at'
  | 'enabled'
  | 'updated_at';

/** Align with instance_agent_resource ColumnDefinition length. */
const FIELD_MAX_LENGTH = {
  resource_name: 128,
  resource_desc: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <span className="text-sm font-medium">
      {children}
      {required ? <span className="text-danger ml-0.5" aria-hidden="true">*</span> : null}
    </span>
  );
}

function summarizeMatchExpr(expr: InstanceAgentResource['match_expr'], allLabel: string): string {
  const text = matchExprToEditorString(expr);
  return text || allLabel;
}

export function InstanceAgentResourceTab({ instanceId }: Props) {
  const { t } = useTranslation();
  const { navigate, path, params } = useRouter();
  const { user } = useAuth();
  const handoffKey = clusterBindingDraftKey(user?.user_id || '', instanceId);
  const focusTemplate = params.get('focusTemplate');
  const requestedTemplate = params.get('template') || params.get('templateId');
  const requestCreate = params.get('create') === '1';
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<AgentInstanceSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: AgentInstanceSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  const { data: rosterData, loading, error: rosterError, reload } = useAsync(
    async () => {
      const filters = {
        page,
        page_size: pageSize,
        search: searchQuery || undefined,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      };
      if (!focusTemplate) return InstanceAgentResourceApi.listInstanceAgentResources(instanceId, filters);
      const all = await collectPages((page, page_size) => InstanceAgentResourceApi.listInstanceAgentResources(instanceId, { ...filters, page, page_size }));
      const matching = all.filter(row => row.ref_template_id === focusTemplate);
      return { items: matching.slice((page - 1) * pageSize, page * pageSize), total: matching.length, page, page_size: pageSize };
    },
    [instanceId, page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder, focusTemplate],
  );
  const catalogState = useAsync(() => collectPages((page, page_size) => AgentTemplateApi.list({ page, page_size })), []);
  const agents = rosterData?.items ?? [];
  const catalog = catalogState.data ?? [];
  useEffect(() => {
    if (rosterData && !loading && page > Math.max(1, Math.ceil(rosterData.total / pageSize))) {
      setPage(Math.max(1, Math.ceil(rosterData.total / pageSize)));
    }
  }, [rosterData, loading, page, pageSize]);
  const templateNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of catalog) {
      map.set(item.template_id, item.template_name);
    }
    return map;
  }, [catalog]);

  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [restoredDraftKey, setRestoredDraftKey] = useState<string | null>(null);
  const draftSaveWarned = useRef(false);

  useEffect(() => {
    if (requestedTemplate || requestCreate) setShowAdd(true);
  }, [requestedTemplate, requestCreate]);

  // Browser back and refresh can also resume a catalog handoff after its action was consumed.
  useEffect(() => {
    try { if (readClusterBindingHandoff(window.sessionStorage, handoffKey)) setShowAdd(true); } catch { /* Storage may be disabled. */ }
  }, [handoffKey]);

  /** 引导跳转：其他页面点「未配置 Agent」跳过来时自动打开添加Agent弹框 */
  useGuideAutoOpen('agentResourceAdd', () => setShowAdd(true));
  const [delTarget, setDelTarget] = useState<InstanceAgentResource | null>(null);
  const [confirmBatchDelete, setConfirmBatchDelete] = useState(false);
  const [addAgentId, setAddAgentId] = useState('');
  const [addResourceName, setAddResourceName] = useState('');
  const [addResourceDesc, setAddResourceDesc] = useState('');
  const [addMatchExpr, setAddMatchExpr] = useState('');
  const [addExpiresAt, setAddExpiresAt] = useState('');
  const [editing, setEditing] = useState<InstanceAgentResource | null>(null);
  const [editResourceName, setEditResourceName] = useState('');
  const [editResourceDesc, setEditResourceDesc] = useState('');
  const [matchExpr, setMatchExpr] = useState('');
  const [editExpiresAt, setEditExpiresAt] = useState('');
  const [busy, setBusy] = useState(false);
  const { markClean: markAddClean, isDirty: isAddDirty } = useFormDirty(showAdd);
  const { markClean: markEditClean, isDirty: isEditDirty } = useFormDirty(!!editing);

  const addDraft = { addAgentId, addResourceName, addResourceDesc, addMatchExpr, addExpiresAt };
  const editDraft = { editResourceName, editResourceDesc, matchExpr, editExpiresAt };

  useEffect(() => {
    setChecked(new Set());
    setPage(1);
  }, [instanceId, focusTemplate]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  useEffect(() => {
    if (!showAdd) { setRestoredDraftKey(null); return; }
    draftSaveWarned.current = false;
    let draft = { ...emptyClusterBindingDraft };
    try { draft = readClusterBindingDraft(window.sessionStorage, handoffKey); } catch { /* Storage may be disabled. */ }
    markAddClean(draft);
    setAddAgentId(draft.addAgentId);
    setAddResourceName(draft.addResourceName);
    setAddResourceDesc(draft.addResourceDesc);
    setAddMatchExpr(draft.addMatchExpr);
    setAddExpiresAt(draft.addExpiresAt);
    setRestoredDraftKey(handoffKey);
  }, [showAdd, markAddClean, handoffKey]);

  const persistAddDraft = useCallback((draft: ClusterBindingDraft) => {
    try {
      if (writeClusterBindingDraft(window.sessionStorage, handoffKey, draft)) return true;
    } catch { /* Browser storage access itself can be disabled. */ }
    if (!draftSaveWarned.current) {
      draftSaveWarned.current = true;
      toast('warn', t('redesign.draftUnavailable', { defaultValue: '无法暂存绑定草稿，返回后需重新填写。' }));
    }
    return false;
  }, [handoffKey, t]);

  // Persist only after this account/cluster's draft has reached the form. Apply
  // an asynchronous catalog choice in the same effect, so stale form state
  // cannot overwrite the returned template in storage before the next render.
  useEffect(() => {
    if (!showAdd || restoredDraftKey !== handoffKey) return;
    let draft = { addAgentId, addResourceName, addResourceDesc, addMatchExpr, addExpiresAt };
    const canConsumeAction = (requestedTemplate || requestCreate) && !catalogState.loading && !!catalogState.data;
    if (canConsumeAction && requestedTemplate) {
      const selected = applyAgentTemplateChoice(draft, requestedTemplate, catalogState.data!);
      draft = selected.draft;
      setAddAgentId(draft.addAgentId);
      setAddResourceName(draft.addResourceName);
      if (selected.error) {
        toast('warn', selected.error === 'disabled'
          ? t('redesign.templateDisabled', { defaultValue: '返回的 Agent 模板已停用，请重新选择可用模板。' })
          : t('redesign.templateNotFound', { defaultValue: '返回的 Agent 模板已不存在，请重新选择。' }));
      }
    }
    persistAddDraft(draft);
    if (canConsumeAction) navigate(consumeAgentBindingAction(path, params), { replace: true });
  }, [showAdd, restoredDraftKey, handoffKey, requestedTemplate, requestCreate, catalogState.loading, catalogState.data, addAgentId, addResourceName, addResourceDesc, addMatchExpr, addExpiresAt, persistAddDraft, params, path, navigate, t]);

  function clearHandoffDraft() {
    setRestoredDraftKey(null);
    try { window.sessionStorage.removeItem(handoffKey); } catch { /* Storage may be disabled. */ }
  }

  function closeAdd() {
    clearHandoffDraft();
    setShowAdd(false);
    if (requestCreate || requestedTemplate) navigate(consumeAgentBindingAction(path, params), { replace: true });
  }

  useEffect(() => {
    if (editing) {
      const nextMatch = matchExprToEditorString(editing.match_expr);
      const nextName = clipField(editing.resource_name ?? '', FIELD_MAX_LENGTH.resource_name);
      const nextDesc = clipField(editing.resource_desc ?? '', FIELD_MAX_LENGTH.resource_desc);
      let nextExpires = '';
      if (editing.expires_at) {
        const d = new Date(editing.expires_at);
        nextExpires = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
      setMatchExpr(nextMatch);
      setEditResourceName(nextName);
      setEditResourceDesc(nextDesc);
      setEditExpiresAt(nextExpires);
      markEditClean({
        editResourceName: nextName,
        editResourceDesc: nextDesc,
        matchExpr: nextMatch,
        editExpiresAt: nextExpires,
      });
    } else {
      setEditResourceName('');
      setEditResourceDesc('');
      setEditExpiresAt('');
    }
  }, [editing, instanceId, markEditClean]);

  const candidates = catalog.map((b) => ({ id: b.template_id, label: b.template_name, sub: b.template_id, enabled: b.enabled }));

  function templateLabel(refTemplateId: string): string {
    return templateNameById.get(refTemplateId) || refTemplateId;
  }

  function toggleCheck(id: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setChecked((prev) => (prev.size === agents.length ? new Set() : new Set(agents.map((a) => a.resource_id))));
  }

  async function removeSelected() {
    const ids = Array.from(checked);
    if (!ids.length) return;
    try {
      for (const id of ids) {
        const row = agents.find((item) => item.resource_id === id);
        if (!row) continue;
        await InstanceAgentResourceApi.remove(instanceId, row.resource_id);
      }
      toast('success', t('success.saved'));
      setChecked(new Set());
      reload();
      bumpGuideRevision();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function saveInstanceAgentResource() {
    if (!editing) return;
    if (!editResourceName.trim()) {
      toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.resourceName') }));
      return;
    }
    const err = validateMatchExprModel(parseMatchExpr(matchExpr), AGENT_RESOURCE_MATCH_FIELDS);
    if (err) {
      toast('danger', t('policies.matchExpr.invalid'));
      return;
    }
    setBusy(true);
    try {
      await InstanceAgentResourceApi.update(instanceId, editing.resource_id, {
        match_exprs: [matchExpr],
        resource_name: editResourceName.trim(),
        resource_desc: editResourceDesc.trim() || null,
        expires_at: editExpiresAt ? new Date(editExpiresAt).toISOString() : null,
      });
      toast('success', t('success.saved'));
      setEditing(null);
      reload();
      bumpGuideRevision();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const allMatchLabel = t('instanceDetail.resourcePanel.agent.scopeAll');

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] shrink-0">
          <div className="page-title truncate">{t('instanceDetail.resourcePanel.agent.title')}</div>
          <div className="page-subtitle truncate">{t('instanceDetail.resourcePanel.agent.subtitle')}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          {checked.size === 0 ? (
            <>
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder={t('instanceDetail.resourcePanel.agent.searchPlaceholder')}
                className="basis-full sm:basis-auto"
              />
              <button className="btn sm" onClick={() => void reload()}>
                {t('common.refresh')}
              </button>
              <button className="btn primary sm" onClick={() => setShowAdd(true)}>
                + {t('instanceDetail.resourcePanel.agent.add')}
              </button>
            </>
          ) : (
            <button className="btn danger sm" onClick={() => setConfirmBatchDelete(true)}>
              {t('common.delete')}({checked.size})
            </button>
          )}
        </div>
      </div>
      <div className="card !p-0">
        {focusTemplate && <div className="template-filter-note flex justify-between items-center gap-2"><span>{t('redesign.clusterTemplateFilter', { defaultValue: '仅显示此模板的绑定' })} · {templateLabel(focusTemplate)}</span><button className="btn ghost sm" onClick={() => { const next = new URLSearchParams(params); next.delete('focusTemplate'); navigate(`${path}${next.size ? `?${next}` : ''}`, { replace: true }); }}>{t('redesign.showAllBindings', { defaultValue: '查看全部绑定' })}</button></div>}
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : rosterError ? (
          <div className="p-4 text-sm text-danger" role="alert">{t('errors.loadFailed', { detail: rosterError })}<button type="button" className="btn sm ml-2" onClick={() => void reload()}>{t('common.refresh')}</button></div>
        ) : (
          <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={agents.length > 0 && checked.size === agents.length} onChange={toggleAll} />
                </th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.resourceName')}
                    value={sortBy === 'resource_id' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('resource_id', value)}
                  />
                </th>
                <th>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.agentTemplate')}
                    value={sortBy === 'template_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('template_name', value)}
                  />
                </th>
                <th>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.grantedBy')}
                    value={sortBy === 'granted_by' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('granted_by', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('instanceDetail.resourcePanel.agent.expiresAt')}
                    value={sortBy === 'expires_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('expires_at', value)}
                  />
                </th>
                <th>
                  <div className="th-filter">
                    <span className="th-filter__label">{t('common.enabled')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('common.enabled')}
                      value={sortBy === 'enabled' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => handleSortChange('enabled', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('common.enabled')}
                      value={enabledFilter}
                      options={[
                        { value: '', label: t('common.all') },
                        { value: 'true', label: t('common.enabled') },
                        { value: 'false', label: t('common.disabled') },
                      ]}
                      onChange={(value) => {
                        setEnabledFilter(value);
                        setPage(1);
                      }}
                    />
                  </div>
                </th>
                <th>
                  <TableColumnSort
                    label={t('common.updatedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => handleSortChange('updated_at', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[6rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {agents.length === 0 ? (
                <tr><td colSpan={10}><Empty text={t('common.empty')} /></td></tr>
              ) : agents.map((a) => {
                const tplName = templateLabel(a.ref_template_id);
                const scopeText = summarizeMatchExpr(a.match_expr, allMatchLabel);
                return (
                  <tr key={a.resource_id}>
                    <td><input type="checkbox" checked={checked.has(a.resource_id)} onChange={() => toggleCheck(a.resource_id)} /></td>
                    <td className="align-top">
                      <div className="text-text-strong font-medium break-words">
                        {a.resource_name || a.resource_id}
                      </div>
                      <div className="text-[11px] text-muted mono break-all" title={a.resource_id}>
                        {a.resource_id}
                      </div>
                    </td>
                    <td className="text-[11px] text-muted break-words max-w-[14rem] align-top">
                      {a.resource_desc || '-'}
                    </td>
                    <td className="align-top">
                      <a className="agent-name-link break-words" href={`/manager/agent-templates/${encodeURIComponent(a.ref_template_id)}?${new URLSearchParams({ cluster: instanceId, returnTo: agentPreparationCatalogPath(instanceId, params), ...(params.get('setup') === '1' ? { setup: '1' } : {}) })}`} target="_blank" rel="noopener noreferrer">{tplName} ↗</a>
                      <div className="text-[11px] text-muted mono break-all" title={a.ref_template_id}>{a.ref_template_id}</div>
                    </td>
                    <td className="mono text-[11px] text-muted max-w-[14rem]" title={scopeText}>
                      {scopeText}
                    </td>
                    <td className="text-[11px] text-muted whitespace-nowrap">{a.granted_by ?? '-'}</td>
                    <td className="text-[11px] text-muted whitespace-nowrap">{a.expires_at ? formatTime(a.expires_at) : t('instanceDetail.resourcePanel.agent.neverExpires')}</td>
                    <td className="whitespace-nowrap">
                      <Switch
                        checked={a.enabled !== false}
                        onChange={(enabled) => {
                          InstanceAgentResourceApi.update(instanceId, a.resource_id, {
                            match_exprs: [matchExprToEditorString(a.match_expr)],
                            resource_name: (a.resource_name ?? '').trim() || a.resource_id,
                            resource_desc: a.resource_desc ?? null,
                            enabled,
                          }).then(() => { toast('success', t('success.saved')); reload(); bumpGuideRevision(); })
                            .catch((e) => toast('danger', e instanceof ApiError ? e.detail : String(e)));
                        }}
                        aria-label={a.enabled !== false ? t('common.enabled') : t('common.disabled')}
                      />
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap">{formatTime(a.updated_at)}</td>
                    <td className="whitespace-nowrap min-w-[9.5rem]">
                      <div className="flex items-center gap-1">
                        <button className="btn sm ghost" onClick={() => setEditing(a)}>{t('common.edit')}</button>
                        <button className="btn sm danger" onClick={() => setDelTarget(a)}>{t('common.delete')}</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {rosterData && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={rosterData.total ?? agents.length}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      )}

      {showAdd && (
        <Modal
          open
          size="lg"
          title={t('instanceDetail.resourcePanel.agent.add')}
          onClose={closeAdd}
          dirty={isAddDirty(addDraft)}
          footer={
            <>
              <ModalCancelButton className="btn" />
              <button
                className="btn primary"
                style={{ marginLeft: 8 }}
                disabled={busy || restoredDraftKey !== handoffKey || !!requestedTemplate || catalogState.loading || !!catalogState.error || !catalog.some(item => item.template_id === addAgentId && item.enabled) || !addResourceName.trim()}
                onClick={async () => {
                  if (busy || instanceId === 'mock-cluster-demo') return;
                  if (!addAgentId) {
                    toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.agentLabel') }));
                    return;
                  }
                  if (!addResourceName.trim()) {
                    toast('warn', t('policies.fieldRequired', { field: t('instanceDetail.resourcePanel.agent.resourceName') }));
                    return;
                  }
                  const err = validateMatchExprModel(parseMatchExpr(addMatchExpr), AGENT_RESOURCE_MATCH_FIELDS);
                  if (err) { toast('danger', t('policies.matchExpr.invalid')); return; }
                  setBusy(true);
                  try {
                    await InstanceAgentResourceApi.create(instanceId, {
                      ref_template_id: addAgentId,
                      match_exprs: [addMatchExpr],
                      resource_name: addResourceName.trim(),
                      resource_desc: addResourceDesc.trim() || null,
                      expires_at: addExpiresAt ? new Date(addExpiresAt).toISOString() : null,
                    });
                    toast('success', t('success.saved'));
                    clearHandoffDraft();
                    markAddClean(addDraft);
                    setShowAdd(false);
                    setAddAgentId('');
                    setAddResourceName('');
                    setAddResourceDesc('');
                    bumpGuideRevision();
                    setAddMatchExpr('');
                    setAddExpiresAt('');
                    reload();
                  } catch (e) {
                    toast('danger', e instanceof ApiError ? e.detail : String(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {t('common.confirm')}
              </button>
            </>
          }
        >
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.resourceName')}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={addResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setAddResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</FieldLabel>
            <textarea
              className="textarea mt-1 w-full min-h-[76px]"
              value={addResourceDesc}
              maxLength={FIELD_MAX_LENGTH.resource_desc}
              onChange={(e) => setAddResourceDesc(clipField(e.target.value, FIELD_MAX_LENGTH.resource_desc))}
            />
            <div className="text-[11px] text-muted mt-1 text-right">
              {addResourceDesc.length}/{FIELD_MAX_LENGTH.resource_desc}
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.agentLabel')}</FieldLabel>
            <select
              className="select mt-1 w-full"
              value={addAgentId}
              disabled={catalogState.loading || !!catalogState.error || !!requestedTemplate}
              onChange={(e) => {
                const selectedTemplateId = e.target.value;
                setAddAgentId(selectedTemplateId);
                const selected = catalog.find((item) => item.template_id === selectedTemplateId);
                if (selected && !addResourceName.trim()) {
                  setAddResourceName(clipField(selected.template_name || '', FIELD_MAX_LENGTH.resource_name));
                }
              }}
            >
              <option value="">{t('common.pleaseSelect')}</option>
              {candidates.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.enabled}>{c.label}{!c.enabled ? ` · ${t('common.disabled')}` : ''}</option>
              ))}
            </select>
            <div className="mt-1">
              <GuideLink page={t('nav.agentManagement')} to={agentPreparationCatalogPath(instanceId, params, true)} onNavigate={() => { if (persistAddDraft(addDraft)) markAddClean(addDraft); }} />
            </div>
            {catalogState.loading && <p className="text-xs text-muted mt-2" role="status">{t('common.loading')}</p>}
            {catalogState.error && <div className="text-xs text-danger mt-2" role="alert">{t('errors.loadFailed', { detail: catalogState.error })}<button type="button" className="btn ghost sm" onClick={() => void catalogState.reload()}>{t('common.refresh')}</button></div>}
          </label>
          {addAgentId && <AgentExpertPreviewSummary templateId={addAgentId} />}
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor
                value={addMatchExpr}
                onChange={setAddMatchExpr}
                allowedFields={AGENT_RESOURCE_MATCH_FIELDS}
              />
            </div>
          </label>
          <label className="block">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.expiresAt')}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={addExpiresAt}
              onChange={(e) => setAddExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t('instanceDetail.resourcePanel.agent.expiresHint')}</div>
          </label>
          <ConfigRefreshCostHint />
        </Modal>
      )}

      {editing && (
        <Modal
          open
          size="lg"
          title={t('instanceDetail.resourcePanel.agent.editGrant', {
            name: editing.resource_name || templateLabel(editing.ref_template_id),
          })}
          onClose={() => setEditing(null)}
          dirty={isEditDirty(editDraft)}
          footer={
            <>
              <ModalCancelButton className="btn" />
              <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy} onClick={() => void saveInstanceAgentResource()}>
                {t('common.save')}
              </button>
            </>
          }
        >
          <label className="block mb-3">
            <FieldLabel required>{t('instanceDetail.resourcePanel.agent.resourceName')}</FieldLabel>
            <LimitedTextInput
              className="mt-1"
              value={editResourceName}
              maxLength={FIELD_MAX_LENGTH.resource_name}
              onChange={setEditResourceName}
            />
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.resourceDesc')}</FieldLabel>
            <textarea
              className="textarea mt-1 w-full min-h-[76px]"
              value={editResourceDesc}
              maxLength={FIELD_MAX_LENGTH.resource_desc}
              onChange={(e) => setEditResourceDesc(clipField(e.target.value, FIELD_MAX_LENGTH.resource_desc))}
            />
            <div className="text-[11px] text-muted mt-1 text-right">
              {editResourceDesc.length}/{FIELD_MAX_LENGTH.resource_desc}
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.agentLabel')}</FieldLabel>
            <div className="input mt-1 w-full !bg-[var(--bg-muted)] cursor-not-allowed">
              {templateLabel(editing.ref_template_id)}({editing.ref_template_id})
            </div>
          </label>
          <AgentExpertPreviewSummary templateId={editing.ref_template_id} />
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.scopeLabel')}</FieldLabel>
            <div className="mt-1">
              <MatchExprEditor
                key={editing.resource_id}
                value={matchExpr}
                onChange={setMatchExpr}
                allowedFields={AGENT_RESOURCE_MATCH_FIELDS}
              />
            </div>
          </label>
          <label className="block mb-3">
            <FieldLabel>{t('instanceDetail.resourcePanel.agent.expiresAt')}</FieldLabel>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={editExpiresAt}
              onChange={(e) => setEditExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">{t('instanceDetail.resourcePanel.agent.expiresHint')}</div>
          </label>
          <ConfigRefreshCostHint />
        </Modal>
      )}

      <ConfirmDialog
        open={confirmBatchDelete}
        message={t('instanceDetail.resourcePanel.agent.confirmRemove', { n: checked.size })}
        danger
        costHint
        onConfirm={async () => {
          await removeSelected();
          setConfirmBatchDelete(false);
        }}
        onClose={() => setConfirmBatchDelete(false)}
      />

      <ConfirmDialog
        open={!!delTarget}
        message={t('instanceDetail.resourcePanel.agent.confirmRemove', { n: 1 })}
        danger
        costHint
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await InstanceAgentResourceApi.remove(instanceId, delTarget.resource_id);
            toast('success', t('success.saved'));
            void reload();
            bumpGuideRevision();
          } catch (e) {
            toast('danger', e instanceof ApiError ? e.detail : String(e));
          } finally {
            setDelTarget(null);
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </div>
  );
}
