import { useEffect, useState, type ReactNode } from 'react';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Pagination } from '../../components/Pagination';
import { Switch } from '../../components/Switch';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { A2AAccessPolicyTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { A2AAccessPolicyMode, A2AAccessPolicyTemplate } from '../../types';
import { formatTime, truncate } from '../../utils/format';
import { A2AAccessPolicyModal } from './A2AAccessPolicyModal';

export function A2AAccessPoliciesPage({ header, tabs }: { header?: ReactNode; tabs?: ReactNode }) {
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(20);
  const [mode, setMode] = useState<A2AAccessPolicyMode | ''>(''); const [enabled, setEnabled] = useState('');
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const { data, loading, error, reload } = useAsync(() => A2AAccessPolicyTemplateApi.list({ page, page_size: pageSize, search: searchQuery, mode: mode || undefined, enabled: enabled === '' ? undefined : enabled === 'true' }), [page, pageSize, searchQuery, mode, enabled]);
  const [items, setItems] = useState<A2AAccessPolicyTemplate[]>([]);
  const [editing, setEditing] = useState<A2AAccessPolicyTemplate | null>(null); const [modalOpen, setModalOpen] = useState(false); const [deleting, setDeleting] = useState<A2AAccessPolicyTemplate | null>(null);
  const [disabling, setDisabling] = useState<A2AAccessPolicyTemplate | null>(null);
  useEffect(() => setItems(data?.items ?? []), [data]);
  const action = async (fn: () => Promise<unknown>) => { try { await fn(); toast('success', '操作成功'); void reload(); } catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); } };
  return <>
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 items-start">{header}<div className="a2a-header-actions flex min-w-0 flex-nowrap items-center justify-end gap-2">
        <ListSearchInput value={searchInput} onChange={setSearchInput} placeholder="搜索策略名称或描述" className="a2a-header-search py-0" />
        <select className="select !w-auto shrink-0 py-0" value={mode} onChange={(e) => { setMode(e.target.value as A2AAccessPolicyMode | ''); setPage(1); }}><option value="">全部模式</option><option value="allowlist">白名单</option><option value="denylist">黑名单</option></select>
        <select className="select !w-auto shrink-0 py-0" value={enabled} onChange={(e) => { setEnabled(e.target.value); setPage(1); }}><option value="">全部状态</option><option value="true">已启用</option><option value="false">已停用</option></select>
        <button className="btn sm shrink-0" onClick={() => void reload()}>刷新</button><button className="btn primary sm shrink-0" onClick={() => { setEditing(null); setModalOpen(true); }}>+ 新建访问策略</button>
      </div></div>
      {tabs}
      <div className="card !p-0 overflow-x-auto">{loading ? <div className="p-4 text-muted">加载中…</div> : error ? <div className="p-4 text-danger">{error}</div> : <table className="table min-w-full"><thead><tr><th>策略</th><th>模式</th><th>成员数</th><th>引用数</th><th>状态</th><th>版本 / 更新时间</th><th>操作</th></tr></thead><tbody>
        {!items.length ? <tr><td colSpan={7}><Empty text="暂无访问策略" /></td></tr> : items.map((row) => <tr key={row.policy_id}>
          <td><div className="font-medium">{row.policy_name}</div><div className="text-xs text-muted">{truncate(row.description || '', 42) || '—'}</div></td><td>{row.mode === 'allowlist' ? '白名单' : '黑名单'}</td><td>{row.member_template_ids.length}</td><td>{row.reference_count}</td>
          <td><Switch checked={row.enabled} onChange={(value) => value || !row.reference_count ? void action(() => A2AAccessPolicyTemplateApi.update(row.policy_id, { enabled: value })) : setDisabling(row)} /></td><td><span className="badge">r{row.revision}</span><div className="text-xs text-muted">{formatTime(row.updated_at)}</div></td>
          <td className="whitespace-nowrap"><button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>编辑</button><button className="btn sm danger ml-1" onClick={() => row.reference_count ? toast('warn', `该策略被 ${row.reference_count} 个 Agent 模板引用，无法删除。`) : setDeleting(row)}>删除</button></td></tr>)}</tbody></table>}
      </div>{data && <Pagination page={page} pageSize={pageSize} total={data.total} onChange={(p, ps) => { setPage(p); setPageSize(ps); }} />}
    </div>
    <A2AAccessPolicyModal open={modalOpen} policy={editing} onClose={() => setModalOpen(false)} onSaved={() => { setModalOpen(false); void reload(); }} />
    <ConfirmDialog open={!!deleting} message="确认删除该访问策略？" danger onClose={() => setDeleting(null)} onConfirm={() => deleting && action(() => A2AAccessPolicyTemplateApi.remove(deleting.policy_id)).finally(() => setDeleting(null))} />
    <ConfirmDialog open={!!disabling} message={`该策略被 ${disabling?.reference_count ?? 0} 个 Agent 模板引用。停用后，这些 Agent 将拒绝全部 A2A 调用，确认停用？`} danger onClose={() => setDisabling(null)} onConfirm={() => disabling && action(() => A2AAccessPolicyTemplateApi.update(disabling.policy_id, { enabled: false })).finally(() => setDisabling(null))} />
  </>;
}
