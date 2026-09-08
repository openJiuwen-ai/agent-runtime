import { useEffect, useState, type ReactNode } from 'react';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Pagination } from '../../components/Pagination';
import { Switch } from '../../components/Switch';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { A2AOutboundTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { A2AOutboundTemplate } from '../../types';
import { formatTime, truncate } from '../../utils/format';
import { A2AOutboundTemplateModal } from './A2AOutboundTemplateModal';
import { A2ADiscoverySettingsModal } from './A2ADiscoverySettingsModal';

export function A2AOutboundTemplatesPage({ embedded = false, header, tabs }: { embedded?: boolean; header?: ReactNode; tabs?: ReactNode }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const { data, loading, error, reload } = useAsync(
    () => A2AOutboundTemplateApi.list({ page, page_size: pageSize, search: searchQuery }),
    [page, pageSize, searchQuery],
  );
  const [items, setItems] = useState<A2AOutboundTemplate[]>([]);
  const [editing, setEditing] = useState<A2AOutboundTemplate | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleting, setDeleting] = useState<A2AOutboundTemplate | null>(null);
  useEffect(() => setItems(data?.items ?? []), [data]);

  const action = async (fn: () => Promise<unknown>) => {
    try { await fn(); toast('success', '操作成功'); void reload(); }
    catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); }
  };

  return <>
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 items-start">
        {header ?? (!embedded && <div><div className="page-title">A2A 管理</div><div className="page-subtitle">发现、注册并维护企业可用的出站 A2A Agent</div></div>)}
        <div className="a2a-header-actions flex min-w-0 flex-nowrap items-center justify-end gap-2"><ListSearchInput value={searchInput} onChange={setSearchInput} placeholder="搜索名称、地址或标签" className="a2a-header-search py-0" /><button className="btn sm shrink-0" onClick={() => void reload()}>刷新</button><button className="btn sm shrink-0" onClick={() => setSettingsOpen(true)}>网络访问设置</button><button className="btn primary sm shrink-0" onClick={() => { setEditing(null); setModalOpen(true); }}>+ 注册 A2A Agent</button></div>
      </div>
      {tabs}
      <div className="card !p-0 overflow-x-auto">
        {loading ? <div className="p-4 text-muted">加载中…</div> : error ? <div className="p-4 text-danger">{error}</div> :
          <table className="table min-w-full"><thead><tr><th>Agent</th><th>地址 / 接口</th><th>Card 版本</th><th>状态</th><th>最近检查</th><th>操作</th></tr></thead><tbody>
          {!items.length ? <tr><td colSpan={6}><Empty text="暂无 A2A Agent" /></td></tr> : items.map((row) => <tr key={row.template_id}>
            <td><div className="font-medium">{row.template_name}</div><div className="text-xs text-muted">{truncate(row.description || '', 42) || '—'}</div></td>
            <td><div className="mono text-xs break-all">{row.source_url}</div><div className="text-xs text-muted">{String(row.selected_interface.protocol_binding || '')}</div></td>
            <td><span className="badge">r{row.card_revision}</span>{row.pending_revision && <span className="badge ml-1 text-warning">待确认</span>} {row.last_error_code && <div className="text-xs text-danger">{row.last_error_summary}</div>}</td>
            <td><Switch checked={row.enabled} onChange={(enabled) => void action(() => A2AOutboundTemplateApi.update(row.template_id, { enabled }))} /></td>
            <td className="text-xs text-muted whitespace-nowrap">{formatTime(row.last_checked_at)}</td>
            <td className="whitespace-nowrap"><div className="flex gap-1"><button className="btn sm ghost" onClick={() => void action(() => A2AOutboundTemplateApi.refresh(row.template_id))}>检查 Card</button>{row.pending_revision && <><button className="btn sm" onClick={() => void action(() => A2AOutboundTemplateApi.confirmRevision(row.template_id, true))}>接受</button><button className="btn sm ghost" onClick={() => void action(() => A2AOutboundTemplateApi.confirmRevision(row.template_id, false))}>拒绝</button></>}<button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>编辑</button><button className="btn sm danger" onClick={() => setDeleting(row)}>删除</button></div></td>
          </tr>)}</tbody></table>}
      </div>
      {data && <Pagination page={page} pageSize={pageSize} total={data.total} onChange={(p, ps) => { setPage(p); setPageSize(ps); }} />}
    </div>
    <A2AOutboundTemplateModal open={modalOpen} template={editing} onClose={() => setModalOpen(false)} onSaved={() => { setModalOpen(false); void reload(); }} />
    <A2ADiscoverySettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onSaved={() => { setSettingsOpen(false); void reload(); }} />
    <ConfirmDialog open={!!deleting} message="确认删除该 A2A Agent？" danger onClose={() => setDeleting(null)} onConfirm={() => deleting && action(() => A2AOutboundTemplateApi.remove(deleting.template_id)).finally(() => setDeleting(null))} />
  </>;
}
