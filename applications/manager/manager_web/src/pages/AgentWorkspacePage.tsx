import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentClientRegistrationApi, AgentTemplateApi, ApiError, InstanceApi, type AgentTemplate } from '../services/api';
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
import { ResourceImportButton, ResourceExportButton } from '../components/ResourceImportExport';
import { WorkspaceNote } from '../components/WorkspaceNote';
import { formatTime } from '../utils/format';
import { toast } from '../stores/uiStore';
import { bumpGuideRevision } from '../stores/guideStore';
import { agentPreparationCluster, agentClusterReturnPath } from '../utils/agentFlowContext';

const catalogPath = '/agent-templates';
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
  const sort = params.get('sort') || 'updated_at:desc';
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
  const { data: registrationConfig } = useAsync(AgentClientRegistrationApi.capabilities);
  useEffect(() => setSearchInput(search), [search]);

  const updateQuery = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    navigate(`${catalogPath}${next.size ? `?${next}` : ''}`, { replace: true });
  };
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
    return loadAgentTemplateCatalog({
      page, page_size: pageSize, search,
      enabled: status === 'enabled' ? true : status === 'disabled' ? false : undefined,
      sort_by: ['template_name', 'description', 'template_id', 'updated_at'].includes(sortField) ? sortField as 'template_name' | 'description' | 'template_id' | 'updated_at' : 'updated_at',
      sort_order: sortDirection === 'asc' ? 'asc' : 'desc',
    }, refType, refId);
  }, [page, pageSize, search, status, sort, refType, refId]);
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
  const mutate = async (target: NonNullable<typeof confirm>) => {
    if (busy) return;
    setBusy(true);
    try {
      if (target.action === 'delete') await AgentTemplateApi.remove(target.row.template_id);
      else await AgentTemplateApi.update(target.row.template_id, { enabled: !target.row.enabled });
      if (saved?.template_id === target.row.template_id) setSaved(null);
      setMore(null);
      setConfirm(null);
      bumpGuideRevision();
      toast('success', t(target.action === 'delete' ? 'success.deleted' : 'success.saved'));
      await result.reload();
    } catch (error) {
      toast('danger', error instanceof ApiError ? error.detail : (error as Error).message);
    } finally { setBusy(false); }
  };
  const clearFilters = () => { setSearchInput(''); updateQuery({ search: null, status: null, refType: null, refId: null, refName: null, page: null }); };
  const filtered = !!(search || status || refType || refId);

  return <div className="agent-workspace">
    <div className="page-header agent-workspace-header">
      <div><h1 className="page-title">{tr('Agent模板管理', 'Agent templates')}</h1><p className="page-subtitle">{tr('组合模型、技能与工具，创建可在多个集群复用的 Agent 模板。', 'Compose models, skills and tools into Agent templates reusable across clusters.')}</p></div>
      <div className="agent-workspace-actions">
        <ResourceImportButton resourceType="agent-template" onImported={() => { bumpGuideRevision(); void result.reload(); }} />
        <button className="btn primary" onClick={create}>+ {tr('新建 Agent模板', 'New Agent template')}</button>
      </div>
    </div>
    {cluster && <div className="template-context" role="status"><div><strong>{tr('正在为集群准备模板', 'Preparing a template for a cluster')}{context.data ? ` · ${context.data.jiuwenclaw_name}` : ''}</strong><p>{context.error ? tr('集群信息读取失败。可继续管理模板，返回集群后重试。', 'Could not load cluster information. You can manage templates and retry in the cluster.') : tr('新建后返回此集群继续绑定，也可以直接选择下方已有模板。', 'Create a template and return to bind it, or select an existing template below.')}</p></div><button className="btn sm" onClick={() => returnToCluster()}>{tr('返回集群配置', 'Return to cluster')}</button></div>}
    {saved && <div className="template-context template-saved" role="status"><div><strong>{tr('模板已保存', 'Template saved')} · {saved.template_name}</strong><p>{tr('接下来将模板绑定到集群，并在集群内检查准入和运行资源。', 'Next, bind it to a cluster and check admission and runtime resources there.')}</p></div><div className="flex flex-wrap gap-2"><button className="btn primary sm" onClick={() => navigate(bindPath(saved.template_id))}>{tr('绑定已有集群', 'Bind to a cluster')}</button><button className="btn sm" onClick={() => navigate(`/configuration-wizard?${new URLSearchParams({ template: saved.template_id, returnTo: currentPath })}`)}>{tr('新建集群并配置', 'Create and configure cluster')}</button><button className="btn ghost sm" onClick={() => setSaved(null)}>{tr('稍后配置', 'Later')}</button></div></div>}
    <section className="card agent-list-panel" aria-label={tr('Agent模板列表', 'Agent template list')}>
      <form className="agent-list-toolbar" onSubmit={event => { event.preventDefault(); updateQuery({ search: searchInput.trim(), page: null }); }}>
        <label className="agent-list-search"><span className="sr-only">{tr('搜索模板', 'Search templates')}</span><input className="input" placeholder={tr('搜索模板名称、描述、标签或 ID', 'Search name, description, tags or ID')} value={searchInput} onChange={event => setSearchInput(event.target.value)} /></label>
        <button className="btn" type="submit">{tr('搜索', 'Search')}</button>
        <label><span className="sr-only">{tr('模板状态', 'Template status')}</span><select className="input" value={status} onChange={event => updateQuery({ status: event.target.value, page: null })}><option value="">{tr('全部状态', 'All statuses')}</option><option value="enabled">{tr('已启用', 'Enabled')}</option><option value="disabled">{tr('已停用', 'Disabled')}</option></select></label>
        <label><span className="sr-only">{tr('排序', 'Sort')}</span><select className="input" value={sort} onChange={event => updateQuery({ sort: event.target.value, page: null })}>{[['updated_at:desc', tr('最近更新', 'Recently updated')], ['updated_at:asc', tr('最早更新', 'Oldest updates')], ['template_name:asc', tr('名称升序', 'Name ascending')], ['template_name:desc', tr('名称降序', 'Name descending')], ['description:asc', tr('描述升序', 'Description ascending')], ['description:desc', tr('描述降序', 'Description descending')], ['template_id:asc', tr('ID 升序', 'ID ascending')], ['template_id:desc', tr('ID 降序', 'ID descending')]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <button className="btn ghost" type="button" disabled={result.loading} onClick={() => void result.reload()}>{tr('刷新', 'Refresh')}</button>
        {filtered && <button className="btn ghost" type="button" onClick={clearFilters}>{tr('清除筛选', 'Clear filters')}</button>}
      </form>
      {(refType || refId) && <div className="template-filter-note flex justify-between items-center gap-2"><span>{tr('正在查看引用此资源的模板：', 'Templates referencing: ')}<strong>{params.get('refName') || refId || refType}</strong></span>{resourceCatalog && <button className="btn ghost sm" onClick={() => navigate(resourceCatalog)}>{tr('返回资源目录', 'Return to resource catalog')}</button>}</div>}
      {result.error ? <div className="agent-list-message" role="alert"><p className="text-danger">{result.error}</p><button className="btn mt-3" onClick={() => void result.reload()}>{tr('重试', 'Retry')}</button></div> : result.loading ? <div className="agent-list-message" role="status">{tr('正在读取模板…', 'Loading templates…')}</div> : !result.data?.items.length ? <div className="overview-empty"><h3>{filtered ? tr('没有匹配的模板', 'No matching templates') : tr('创建第一个 Agent 模板', 'Create your first Agent template')}</h3><p>{filtered ? tr('请调整关键词、状态或资源筛选。', 'Adjust your search, status or resource filter.') : tr('填写名称并选择默认模型即可保存，之后再绑定到集群。', 'Enter a name and select a default model, then bind the saved template to a cluster.')}</p><button className="btn primary" onClick={filtered ? clearFilters : create}>{filtered ? tr('清除筛选', 'Clear filters') : tr('新建 Agent模板', 'New Agent template')}</button></div> : <div className="table-wrap manager-page-table"><table className="table agent-list-table template-catalog-table"><thead><tr><th>{tr('模板名称与用途', 'Template & purpose')}</th><th>{tr('能力配置', 'Capabilities')}</th><th>{tr('模板状态', 'Template status')}</th><th>{tr('更新时间', 'Updated')}</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody>{result.data.items.map(agent => {
        const capabilities = Object.entries(agent.template_ref || {}).filter(([, refs]) => refs.length);
        return <tr key={agent.template_id}>
          <td><button className="agent-name-link" onClick={() => navigate(detailPath(agent.template_id))}>{agent.template_name}</button><p className="agent-list-description">{agent.description || tr('暂无描述', 'No description')}</p>{!!agent.agent_tags?.length && <div className="template-tags">{agent.agent_tags.slice(0, 3).map(tag => <span key={tag}>#{tag}</span>)}{agent.agent_tags.length > 3 && <span>+{agent.agent_tags.length - 3}</span>}</div>}</td>
          <td><div className="agent-list-capabilities">{capabilities.slice(0, 3).map(([slot, refs]) => <span className="tag" key={slot}>{t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}{refs.length > 1 ? ` · ${refs.length}` : ''}</span>)}{capabilities.length > 3 && <button className="btn ghost sm" onClick={() => navigate(detailPath(agent.template_id))}>+{capabilities.length - 3}</button>}{!capabilities.length && <span className="text-muted">{tr('待配置资源', 'No resources configured')}</span>}</div></td>
          <td><span className={`template-state ${agent.enabled ? 'is-enabled' : ''}`}><i aria-hidden="true" />{agent.enabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled')}</span></td>
          <td className="agent-list-time">{formatTime(agent.updated_at)}</td>
          <td><div className="agent-list-actions"><button className="btn ghost sm" onClick={() => { setEditing(agent); setOpen(true); }}>{tr('编辑', 'Edit')}</button><button className="btn ghost sm" disabled={!agent.enabled} title={!agent.enabled ? tr('请先启用模板', 'Enable this template first') : undefined} onClick={() => cluster ? returnToCluster(agent.template_id) : navigate(bindPath(agent.template_id))}>{cluster ? tr('选择并返回集群', 'Select for cluster') : tr('绑定集群', 'Bind to cluster')}</button><button className="btn ghost sm" aria-label={`${tr('更多操作', 'More actions')} · ${agent.template_name}`} onClick={() => setMore(agent)}>···</button></div></td>
        </tr>;
      })}</tbody></table></div>}
      {result.data && <div className="agent-list-pagination"><Pagination page={page} pageSize={pageSize} total={result.data.total} loading={result.loading} error={result.error} onChange={(next, size) => updateQuery({ page: String(next), size: String(size) })} /></div>}
    </section>
    <WorkspaceNote title={tr('模板定义能力，集群决定使用方式', 'Templates define capabilities; clusters define their use')}>
      {tr('这里统一管理 Agent模板（也称 Agent定义）。模板可绑定到多个集群；集群内分别配置准入、运行资源和使用范围。启用模板不代表已部署或可以对话，完成绑定后请到集群配置工作台检查。', 'Agent templates (also called definitions) are managed here and can be reused by multiple clusters. Each cluster configures admission, runtime resources and usage scope. An enabled template is not a deployment or a readiness check; continue in the cluster workspace after binding.')}
    </WorkspaceNote>
    <AgentTemplateModal open={open} template={editing} registrationConfig={registrationConfig} onClose={() => setOpen(false)} onSaved={(agent, options) => {
      void result.reload();
      if (options?.keepOpen) return;
      setOpen(false);
      if (!editing && cluster) returnToCluster(agent.template_id);
      else if (!editing) setSaved(agent);
    }} />
    <Modal open={!!more} title={`${tr('模板操作', 'Template actions')} · ${more?.template_name || ''}`} onClose={() => { if (!busy) setMore(null); }} footer={<button className="btn" disabled={busy} onClick={() => setMore(null)}>{tr('关闭', 'Close')}</button>}>
      {more && <div className="flex flex-col gap-4"><p className="text-muted text-sm">{impact.loading ? tr('正在检查集群引用…', 'Checking cluster bindings…') : impact.error || impact.data?.some(row => row.error) ? tr('部分集群引用读取失败，请先打开详情核对。', 'Some cluster bindings could not be checked. Review details first.') : tr(`该模板共有 ${bindingCount} 条集群绑定。`, `This template has ${bindingCount} cluster bindings.`)}</p><div className="flex flex-wrap gap-2"><button className="btn" onClick={() => navigate(detailPath(more.template_id))}>{tr('查看详情与绑定', 'View details and bindings')}</button><ResourceExportButton resourceType="agent-template" resourceIds={[more.template_id]} /><button className="btn" disabled={busy} onClick={() => { setConfirm({ row: more, action: 'toggle' }); setMore(null); }}>{more.enabled ? tr('停用模板', 'Disable template') : tr('启用模板', 'Enable template')}</button><button className="btn danger" disabled={busy} onClick={() => { setConfirm({ row: more, action: 'delete' }); setMore(null); }}>{tr('删除模板', 'Delete template')}</button></div><p className="text-muted text-sm">{tr('变更模板状态会影响引用它的集群；删除模板会同时清除它在各集群中的绑定。', 'Changing template status affects clusters referencing it. Deleting it also removes its cluster bindings.')}</p></div>}
    </Modal>
    <Modal open={!!confirm} title={tr('确认模板变更', 'Confirm template change')} onClose={() => { if (!busy) setConfirm(null); }} footer={<><button className="btn" disabled={busy} onClick={() => setConfirm(null)}>{tr('取消', 'Cancel')}</button><button className={confirm?.action === 'delete' || confirm?.row.enabled ? 'btn danger' : 'btn primary'} disabled={busy} onClick={() => { if (confirm) void mutate(confirm); }}>{busy ? tr('正在保存…', 'Saving…') : tr('确认', 'Confirm')}</button></>}>
      <p className="text-sm">{confirm?.action === 'delete' ? t('agentTemplate.deleteConfirm', { name: confirm.row.template_name }) : tr(`确认${confirm?.row.enabled ? '停用' : '启用'}模板「${confirm?.row.template_name || ''}」？已引用此模板的集群配置会随之更新。`, `Change the status of "${confirm?.row.template_name || ''}"? Referencing cluster configurations will be updated.`)}</p><ConfigRefreshCostHint scope="template" />
    </Modal>
  </div>;
}
