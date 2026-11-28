import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentTemplateApi, ApiError, InstanceApi, type AgentTemplate } from '../services/api';
import { loadAgentTemplateCatalog } from '../services/agentTemplateCatalog';
import { loadAgentBindings } from '../services/agentWorkspace';
import { agentReferenceSlots, agentResourceCatalogPath } from '../utils/agentTemplateNavigation';
import { useAsync } from '../hooks/useAsync';
import { useGuideAutoOpen } from '../hooks/useGuideAutoOpen';
import { useRouter } from '../router';
import { AgentTemplateModal } from './templates/AgentTemplateModal';
import { Pagination } from '../components/Pagination';
import { Modal } from '../components/Modal';
import { ConfigRefreshCostHint } from '../components/ConfigRefreshCostHint';
import { Empty } from '../components/Empty';
import { Switch } from '../components/Switch';
import { TableColumnFilter } from '../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../components/TableColumnSort';
import { ListSearchInput } from '../components/ListSearchInput';
import { ResourceImportButton, ResourceExportButton } from '../components/ResourceImportExport';
import { WorkspaceNote } from '../components/WorkspaceNote';
import { formatTime, truncate } from '../utils/format';
import { toast } from '../stores/uiStore';
import { bumpGuideRevision } from '../stores/guideStore';
import { templateInUseMessage } from '../utils/templateInUse';
import { agentPreparationCluster, agentClusterReturnPath } from '../utils/agentFlowContext';

const catalogPath = '/agent-templates';
type AgentTemplateSortField = 'template_name' | 'description' | 'template_id' | 'updated_at';

const positive = (value: string | null, fallback: number) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : fallback;
};

export function AgentWorkspacePage() {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { navigate, params } = useRouter();
  const search = params.get('search') || '';
  const status = params.get('status') || '';
  const sort = params.get('sort') || '';
  const [sortField, sortDirection] = sort.split(':');
  const page = positive(params.get('page'), 1);
  const pageSize = [10, 20, 30, 40, 50].includes(Number(params.get('size'))) ? Number(params.get('size')) : 20;
  const refType = params.get('refType');
  const refId = params.get('refId');
  const invalidReference = !!(refType || refId) && (!refType || !refId || !Object.prototype.hasOwnProperty.call(agentReferenceSlots, refType));
  const resourceCatalog = agentResourceCatalogPath(refType);
  const cluster = agentPreparationCluster(params);
  const currentPath = `${catalogPath}${params.size ? `?${params}` : ''}`;
  const [searchInput, setSearchInput] = useState(search);
  const [editing, setEditing] = useState<AgentTemplate | null>(null);
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<AgentTemplate | null>(null);
  const [more, setMore] = useState<AgentTemplate | null>(null);
  const [confirm, setConfirm] = useState<{ row: AgentTemplate; action: 'delete' | 'toggle' } | null>(null);
  const [busy, setBusy] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [items, setItems] = useState<AgentTemplate[]>([]);
  useEffect(() => setSearchInput(search), [search]);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const updateQuery = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    navigate(`${catalogPath}${next.size ? `?${next}` : ''}`, { replace: true });
  };

  const handleSortChange = (field: AgentTemplateSortField, value: ColumnSortValue) => {
    updateQuery({
      sort: value ? `${field}:${value}` : null,
      page: null,
    });
  };

  useEffect(() => {
    if (searchInput.trim() === search) return;
    const timer = window.setTimeout(() => {
      updateQuery({ search: searchInput.trim() || null, page: null });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]); // eslint-disable-line react-hooks/exhaustive-deps
  const create = () => { setEditing(null); setOpen(true); };
  useGuideAutoOpen('agentTemplateNew', create);
  useEffect(() => {
    if (params.get('create') !== '1') return;
    setEditing(null); setOpen(true);
    const next = new URLSearchParams(params); next.delete('create');
    navigate(`${catalogPath}${next.size ? `?${next}` : ''}`, { replace: true });
  }, [params, navigate]);

  const result = useAsync(async () => {
    if (invalidReference) throw new Error(tr('资源筛选参数无效，请清除筛选后重试。', 'Invalid resource filter. Clear it and retry.'));
    const activeSort = ['template_name', 'description', 'template_id', 'updated_at'].includes(sortField)
      ? sortField as AgentTemplateSortField
      : undefined;
    return loadAgentTemplateCatalog({
      page, page_size: pageSize, search,
      enabled: status === 'enabled' ? true : status === 'disabled' ? false : undefined,
      sort_by: activeSort,
      sort_order: activeSort ? (sortDirection === 'asc' ? 'asc' : 'desc') : undefined,
    }, refType, refId);
  }, [page, pageSize, search, status, sort, refType, refId]);

  useEffect(() => {
    if (result.data?.items) setItems(result.data.items);
  }, [result.data]);

  const context = useAsync(async () => cluster ? InstanceApi.get(cluster) : null, [cluster]);
  const impact = useAsync(async () => more ? loadAgentBindings(more.template_id) : [], [more?.template_id]);
  const bindingCount = impact.data?.reduce((n, row) => n + row.resources.length, 0) || 0;
  useEffect(() => {
    if (!result.loading && result.data && page > Math.max(1, Math.ceil(result.data.total / pageSize))) {
      updateQuery({ page: String(Math.max(1, Math.ceil(result.data.total / pageSize))) });
    }
  }, [result.data, result.loading, page, pageSize]); // eslint-disable-line react-hooks/exhaustive-deps

  const templateContext = new URLSearchParams({ returnTo: currentPath, ...(cluster ? { cluster } : {}), ...(params.get('setup') === '1' ? { setup: '1' } : {}) });
  const detailPath = (id: string) => `${catalogPath}/${encodeURIComponent(id)}?${templateContext}`;
  const bindPath = (id: string) => `${catalogPath}/${encodeURIComponent(id)}/connect?${templateContext}`;
  const returnToCluster = (id?: string) => {
    const target = agentClusterReturnPath(params, id);
    if (target) navigate(target);
  };

  const toggleEnabled = async (row: AgentTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await AgentTemplateApi.update(row.template_id, { enabled });
      if (status && enabled !== (status === 'enabled')) {
        setItems((list) => list.filter((item) => item.template_id !== row.template_id));
      }
      bumpGuideRevision();
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) => (item.template_id === row.template_id ? { ...item, enabled: previous } : item)),
      );
      toast('danger', templateInUseMessage(e, t, 'disable') ?? t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  const mutate = async (target: NonNullable<typeof confirm>) => {
    if (busy) return;
    setBusy(true);
    try {
      if (target.action === 'delete') {
        if ((target.row.reference_count ?? 0) > 0) {
          toast('danger', t('agentTemplate.deleteBlockedHint', { count: target.row.reference_count }));
          return;
        }
        await AgentTemplateApi.remove(target.row.template_id);
      }
      else await AgentTemplateApi.update(target.row.template_id, { enabled: !target.row.enabled });
      if (saved?.template_id === target.row.template_id) setSaved(null);
      setMore(null);
      setConfirm(null);
      bumpGuideRevision();
      toast('success', t(target.action === 'delete' ? 'success.deleted' : 'success.saved'));
      await result.reload();
    } catch (error) {
      toast('danger', templateInUseMessage(error, t) ?? (error instanceof ApiError ? error.detail : (error as Error).message));
    } finally { setBusy(false); }
  };
  const clearFilters = () => { setSearchInput(''); updateQuery({ search: null, status: null, refType: null, refId: null, refName: null, page: null }); };
  const filtered = !!(search || status || refType || refId);

  return <div className="manager-page agent-workspace">
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
          <div className="page-title truncate" title={t('agentTemplate.title')}>{t('agentTemplate.title')}</div>
          <div className="page-subtitle truncate" title={t('agentTemplate.subtitle')}>{t('agentTemplate.subtitle')}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('agentTemplate.searchPlaceholder')}
            className="basis-full sm:basis-auto"
          />
          <button className="btn sm" type="button" disabled={result.loading} onClick={() => void result.reload()}>{t('common.refresh')}</button>
          {filtered && <button className="btn sm ghost" type="button" onClick={clearFilters}>{tr('清除筛选', 'Clear filters')}</button>}
          <ResourceImportButton resourceType="agent-template" onImported={() => { bumpGuideRevision(); void result.reload(); }} />
          <button className="btn primary sm" type="button" onClick={create}>+ {t('agentTemplate.new')}</button>
        </div>
      </div>

      {cluster && <div className="template-context" role="status"><div><strong>{tr('正在为集群准备模板', 'Preparing a template for a cluster')}{context.data ? ` · ${context.data.jiuwenclaw_name}` : ''}</strong><p>{context.error ? tr('集群信息读取失败。可继续管理模板，返回集群后重试。', 'Could not load cluster information. You can manage templates and retry in the cluster.') : tr('新建后返回此集群继续绑定，也可以直接选择下方已有模板。', 'Create a template and return to bind it, or select an existing template below.')}</p></div><button className="btn sm" onClick={() => returnToCluster()}>{tr('返回集群配置', 'Return to cluster')}</button></div>}
      {saved && <div className="template-context template-saved" role="status"><div><strong>{tr('模板已保存', 'Template saved')} · {saved.template_name}</strong><p>{tr('接下来将模板绑定到集群，并在集群内检查准入和运行资源。', 'Next, bind it to a cluster and check admission and runtime resources there.')}</p></div><div className="flex flex-wrap gap-2"><button className="btn primary sm" onClick={() => navigate(bindPath(saved.template_id))}>{tr('绑定已有集群', 'Bind to a cluster')}</button><button className="btn sm" onClick={() => navigate(`/configuration-wizard?${new URLSearchParams({ template: saved.template_id, returnTo: currentPath })}`)}>{tr('注册集群并配置', 'Register and configure cluster')}</button><button className="btn ghost sm" onClick={() => setSaved(null)}>{tr('稍后配置', 'Later')}</button></div></div>}
      {(refType || refId) && <div className="template-filter-note flex justify-between items-center gap-2"><span>{tr('正在查看引用此资源的模板：', 'Templates referencing: ')}<strong>{params.get('refName') || refId || refType}</strong></span>{resourceCatalog && <button className="btn ghost sm" onClick={() => navigate(resourceCatalog)}>{tr('返回资源目录', 'Return to resource catalog')}</button>}</div>}

      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
        <div className="card !p-0" aria-label={t('agentTemplate.title')}>
          {result.error ? (
            <div className="p-4 text-sm text-danger" role="alert">
              {result.error}
              <button className="btn sm mt-3" onClick={() => void result.reload()}>{tr('重试', 'Retry')}</button>
            </div>
          ) : result.loading ? (
            <div className="p-4 text-sm text-muted" role="status">{t('common.loading')}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table agent-list-table w-max min-w-full">
                <thead>
                  <tr>
                    <th>
                      <TableColumnSort
                        label={t('agentTemplate.templateName')}
                        value={sortField === 'template_name' ? (sortDirection === 'asc' ? 'asc' : 'desc') : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('template_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('agentTemplate.templateDescription')}
                        value={sortField === 'description' ? (sortDirection === 'asc' ? 'asc' : 'desc') : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('description', value)}
                      />
                    </th>
                    <th>{tr('能力配置', 'Capabilities')}</th>
                    <th>{t('agentTemplate.referenceCount')}</th>
                    <th>
                      <TableColumnFilter
                        label={t('common.enabled')}
                        value={status === 'enabled' ? 'true' : status === 'disabled' ? 'false' : ''}
                        options={[
                          { value: '', label: t('common.all') },
                          { value: 'true', label: t('common.enabled') },
                          { value: 'false', label: t('common.disabled') },
                        ]}
                        onChange={(value) => {
                          updateQuery({
                            status: value === 'true' ? 'enabled' : value === 'false' ? 'disabled' : null,
                            page: null,
                          });
                        }}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('agentTemplate.updatedAt')}
                        value={sortField === 'updated_at' ? (sortDirection === 'asc' ? 'asc' : 'desc') : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('updated_at', value)}
                      />
                    </th>
                    <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={7}>
                        <Empty text={t('common.empty')} />
                      </td>
                    </tr>
                  ) : items.map((agent) => {
                    const capabilities = Object.entries(agent.template_ref || {}).filter(([, refs]) => refs.length);
                    return (
                      <tr key={agent.template_id}>
                        <td className="align-top">
                          <button className="agent-name-link text-text-strong font-medium break-words" onClick={() => navigate(detailPath(agent.template_id))}>
                            {agent.template_name}
                          </button>
                          <div className="text-[11px] text-muted mono break-all" title={agent.template_id}>
                            {agent.template_id}
                          </div>
                        </td>
                        <td className="text-[11px] text-muted max-w-[14rem]" title={agent.description ?? undefined}>
                          {agent.description ? truncate(agent.description, 48) : '—'}
                        </td>
                        <td>
                          <div className="agent-list-capabilities">
                            {capabilities.slice(0, 3).map(([slot, refs]) => (
                              <span className="tag" key={slot}>{t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}{refs.length > 1 ? ` · ${refs.length}` : ''}</span>
                            ))}
                            {capabilities.length > 3 && <button className="btn ghost sm" onClick={() => navigate(detailPath(agent.template_id))}>+{capabilities.length - 3}</button>}
                            {!capabilities.length && <span className="text-muted">{tr('待配置资源', 'No resources configured')}</span>}
                          </div>
                        </td>
                        <td className="whitespace-nowrap">
                          {(agent.reference_count ?? 0) > 0 ? (
                            <span className="tag" title={t('agentTemplate.referenceHint')}>{agent.reference_count}</span>
                          ) : (
                            <span className="text-[11px] text-muted">0</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap">
                          <Switch
                            checked={agent.enabled}
                            disabled={togglingId === agent.template_id}
                            aria-label={agent.enabled ? t('common.enabled') : t('common.disabled')}
                            onChange={(enabled) => void toggleEnabled(agent, enabled)}
                          />
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(agent.updated_at)}
                        </td>
                        <td className="whitespace-nowrap min-w-[9.5rem]">
                          <div className="flex items-center gap-1">
                            <ResourceExportButton resourceType="agent-template" resourceIds={[agent.template_id]} />
                            <button
                              className="btn sm ghost"
                              onClick={() => { setEditing(agent); setOpen(true); }}
                            >
                              {t('common.edit')}
                            </button>
                            <button
                              className="btn sm ghost"
                              disabled={!agent.enabled}
                              title={!agent.enabled ? tr('请先启用模板', 'Enable this template first') : undefined}
                              onClick={() => (cluster ? returnToCluster(agent.template_id) : navigate(bindPath(agent.template_id)))}
                            >
                              {cluster ? tr('选择并返回', 'Select') : tr('绑定集群', 'Bind')}
                            </button>
                            <button
                              className="btn sm ghost"
                              aria-label={`${tr('更多操作', 'More actions')} · ${agent.template_name}`}
                              onClick={() => setMore(agent)}
                            >
                              ···
                            </button>
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

        {result.data && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={result.data.total}
            loading={result.loading}
            error={result.error}
            onChange={(next, size) => updateQuery({ page: String(next), size: String(size) })}
          />
        )}

        <WorkspaceNote title={tr('模板定义能力，集群决定使用方式', 'Templates define capabilities; clusters define their use')}>
          {tr('这里管理 Agent模板，定义模型、技能与工具等能力。准备运行时模板后，请到「其他 → Agent ID」手动创建二者的组合。Agent模板可绑定到多个集群，集群内分别配置准入、运行资源和使用范围。完成绑定后请到集群配置工作台验证。', 'Manage Agent templates that define models, skills and tools. After preparing a runtime template, create their combination under Other → Agent ID. Reuse Agent templates across clusters and configure admission, runtime resources and usage scope in each cluster, then verify in the cluster workspace.')}
        </WorkspaceNote>
      </div>
    </div>
    <AgentTemplateModal open={open} template={editing} onClose={() => setOpen(false)} onSaved={(agent) => {
      void result.reload();
      setOpen(false);
      if (!editing && cluster) returnToCluster(agent.template_id);
      else if (!editing) setSaved(agent);
    }} />
    <Modal open={!!more} title={`${tr('模板操作', 'Template actions')} · ${more?.template_name || ''}`} onClose={() => { if (!busy) setMore(null); }} footer={<button className="btn" disabled={busy} onClick={() => setMore(null)}>{tr('关闭', 'Close')}</button>}>
      {more && <div className="flex flex-col gap-4"><p className="text-muted text-sm">{impact.loading ? tr('正在检查集群引用…', 'Checking cluster bindings…') : impact.error || impact.data?.some(row => row.error) ? tr('部分集群引用读取失败，请先打开详情核对。', 'Some cluster bindings could not be checked. Review details first.') : tr(`该模板共有 ${bindingCount} 条集群绑定。`, `This template has ${bindingCount} cluster bindings.`)}</p><div className="flex flex-wrap gap-2"><button className="btn" onClick={() => navigate(detailPath(more.template_id))}>{tr('查看详情与绑定', 'View details and bindings')}</button><ResourceExportButton resourceType="agent-template" resourceIds={[more.template_id]} /><button className="btn" disabled={busy} onClick={() => { setConfirm({ row: more, action: 'toggle' }); setMore(null); }}>{more.enabled ? tr('停用模板', 'Disable template') : tr('启用模板', 'Enable template')}</button><button className="btn danger" disabled={busy || (more.reference_count ?? 0) > 0} title={(more.reference_count ?? 0) > 0 ? t('agentTemplate.deleteBlockedHint', { count: more.reference_count }) : undefined} onClick={() => { setConfirm({ row: more, action: 'delete' }); setMore(null); }}>{tr('删除模板', 'Delete template')}</button></div><p className="text-muted text-sm">{tr('变更模板状态会影响引用它的集群。仍被 Agent 引用的模板不能删除，请先在集群管理中移除这些 Agent。', 'Changing template status affects clusters referencing it. Templates referenced by Agents cannot be deleted. Remove those Agents in cluster management first.')}</p></div>}
    </Modal>
    <Modal open={!!confirm} title={tr('确认模板变更', 'Confirm template change')} onClose={() => { if (!busy) setConfirm(null); }} footer={<><button className="btn" disabled={busy} onClick={() => setConfirm(null)}>{tr('取消', 'Cancel')}</button><button className={confirm?.action === 'delete' || confirm?.row.enabled ? 'btn danger' : 'btn primary'} disabled={busy} onClick={() => { if (confirm) void mutate(confirm); }}>{busy ? tr('正在保存…', 'Saving…') : tr('确认', 'Confirm')}</button></>}>
      <p className="text-sm">{confirm?.action === 'delete' ? t('agentTemplate.deleteConfirm', { name: confirm.row.template_name }) : tr(`确认${confirm?.row.enabled ? '停用' : '启用'}模板「${confirm?.row.template_name || ''}」？已引用此模板的集群配置会随之更新。`, `Change the status of "${confirm?.row.template_name || ''}"? Referencing cluster configurations will be updated.`)}</p><ConfigRefreshCostHint scope="template" />
    </Modal>
  </div>;
}
