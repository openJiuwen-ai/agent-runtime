import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
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
import { useRouter } from '../../router';
import { A2AOutboundTemplateModal } from './A2AOutboundTemplateModal';
import { A2ADiscoverySettingsModal } from './A2ADiscoverySettingsModal';
import { ResourceExportButton, ResourceImportButton } from '../../components/ResourceImportExport';

/** Manager 短码与 Gateway `A2A_*` 均在词表显式列出；未命中用 summary / code 兜底。 */
function a2aErrorLabel(
  t: ReturnType<typeof useTranslation>['t'],
  i18n: ReturnType<typeof useTranslation>['i18n'],
  code: string,
  summary?: string | null,
): string {
  const path = `a2a.errors.${code}`;
  if (i18n.exists(path)) return t(path);
  return (summary || '').trim() || code;
}

export function A2AOutboundTemplatesPage({ embedded = false, header, tabs }: { embedded?: boolean; header?: ReactNode; tabs?: ReactNode }) {
  const { t, i18n } = useTranslation();
  const { navigate } = useRouter();
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
    try { await fn(); toast('success', t('a2a.actionSuccess')); void reload(); }
    catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); }
  };

  return <>
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 items-start">
        {header ?? (!embedded && <div><div className="page-title">{t('a2a.managementTitle')}</div><div className="page-subtitle">{t('a2a.discoverSubtitle')}</div></div>)}
        <div className="a2a-header-actions flex min-w-0 flex-nowrap items-center justify-end gap-2"><ListSearchInput value={searchInput} onChange={setSearchInput} placeholder={t('a2a.searchAgentsPlaceholder')} className="a2a-header-search py-0" /><button className="btn sm shrink-0" onClick={() => void reload()}>{t('common.refresh')}</button><ResourceImportButton resourceType="a2a-agent" onImported={() => void reload()} className="btn sm shrink-0" /><button className="btn sm shrink-0" onClick={() => setSettingsOpen(true)}>{t('a2a.networkSettings')}</button><button className="btn primary sm shrink-0" onClick={() => { setEditing(null); setModalOpen(true); }}>{t('a2a.registerAgent')}</button></div>
      </div>
      {tabs}
      <div className="card !p-0 overflow-x-auto">
        {loading ? <div className="p-4 text-muted">{t('common.loading')}</div> : error ? <div className="p-4 text-danger">{error}</div> :
          <table className="table min-w-full"><thead><tr><th>{t('a2a.table.agent')}</th><th>{t('a2a.table.address')}</th><th>{t('a2a.table.cardVersion')}</th><th>{t('a2a.table.status')}</th><th>{t('a2a.table.referenceCount')}</th><th>{t('a2a.table.lastChecked')}</th><th>{t('common.actions')}</th></tr></thead><tbody>
          {!items.length ? <tr><td colSpan={7}><Empty text={t('a2a.emptyAgents')} /></td></tr> : items.map((row) => <tr key={row.template_id}>
            <td><div className="font-medium">{row.template_name}</div><div className="text-xs text-muted">{truncate(row.description || '', 42) || '—'}</div></td>
            <td><div className="mono text-xs break-all">{row.source_url}</div><div className="text-xs text-muted">{String(row.selected_interface.protocol_binding || '')}</div></td>
            <td><span className="badge">r{row.card_revision}</span>{row.pending_revision && <span className="badge ml-1 text-warning">{t('a2a.pendingConfirm')}</span>} {row.last_error_code && (
              <div className="text-xs text-danger">
                {a2aErrorLabel(t, i18n, row.last_error_code, row.last_error_summary)}
              </div>
            )}</td>
            <td><Switch checked={row.enabled} onChange={(enabled) => void action(() => A2AOutboundTemplateApi.update(row.template_id, { enabled }))} /></td>
            <td>{row.reference_count > 0 ? <span className="tag cursor-pointer" title={t('a2a.agentReferenceTitle')} onClick={() => navigate('/agent-templates')}>{row.reference_count}</span> : <span className="text-[11px] text-muted">0</span>}</td>
            <td className="text-xs text-muted whitespace-nowrap">{formatTime(row.last_checked_at)}</td>
            <td className="whitespace-nowrap"><div className="flex gap-1"><ResourceExportButton resourceType="a2a-agent" resourceIds={[row.template_id]} /><button className="btn sm ghost" onClick={() => void action(() => A2AOutboundTemplateApi.refresh(row.template_id))}>{t('a2a.checkCard')}</button>{row.pending_revision && <><button className="btn sm" onClick={() => void action(() => A2AOutboundTemplateApi.confirmRevision(row.template_id, true))}>{t('a2a.accept')}</button><button className="btn sm ghost" onClick={() => void action(() => A2AOutboundTemplateApi.confirmRevision(row.template_id, false))}>{t('a2a.reject')}</button></>}<button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>{t('common.edit')}</button><button className="btn sm danger" onClick={() => setDeleting(row)}>{t('common.delete')}</button></div></td>
          </tr>)}</tbody></table>}
      </div>
      {data && <Pagination page={page} pageSize={pageSize} total={data.total} onChange={(p, ps) => { setPage(p); setPageSize(ps); }} />}
    </div>
    <A2AOutboundTemplateModal open={modalOpen} template={editing} onClose={() => setModalOpen(false)} onSaved={() => { setModalOpen(false); void reload(); }} />
    <A2ADiscoverySettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onSaved={() => { setSettingsOpen(false); void reload(); }} />
    <ConfirmDialog open={!!deleting} message={t('a2a.deleteAgentConfirm')} danger onClose={() => setDeleting(null)} onConfirm={() => deleting && action(() => A2AOutboundTemplateApi.remove(deleting.template_id)).finally(() => setDeleting(null))} />
  </>;
}
