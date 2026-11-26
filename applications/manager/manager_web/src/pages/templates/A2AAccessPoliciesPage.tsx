import { agentResourceReferencesPath } from '../../utils/agentTemplateNavigation';
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
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
import { useRouter } from '../../router';
import { A2AAccessPolicyModal } from './A2AAccessPolicyModal';
import { ResourceExportButton, ResourceImportButton } from '../../components/ResourceImportExport';

export function A2AAccessPoliciesPage({ header, tabs }: { header?: ReactNode; tabs?: ReactNode }) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(20);
  const [mode, setMode] = useState<A2AAccessPolicyMode | ''>(''); const [enabled, setEnabled] = useState('');
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const { data, loading, error, reload } = useAsync(() => A2AAccessPolicyTemplateApi.list({ page, page_size: pageSize, search: searchQuery, mode: mode || undefined, enabled: enabled === '' ? undefined : enabled === 'true' }), [page, pageSize, searchQuery, mode, enabled]);
  const [items, setItems] = useState<A2AAccessPolicyTemplate[]>([]);
  const [editing, setEditing] = useState<A2AAccessPolicyTemplate | null>(null); const [modalOpen, setModalOpen] = useState(false); const [deleting, setDeleting] = useState<A2AAccessPolicyTemplate | null>(null);
  const [disabling, setDisabling] = useState<A2AAccessPolicyTemplate | null>(null);
  useEffect(() => setItems(data?.items ?? []), [data]);
  const action = async (fn: () => Promise<unknown>) => { try { await fn(); toast('success', t('a2a.actionSuccess')); void reload(); } catch (e) { toast('danger', e instanceof ApiError ? e.detail : (e as Error).message); } };
  return <>
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 items-start">{header}<div className="a2a-header-actions flex min-w-0 flex-nowrap items-center justify-end gap-2">
        <ListSearchInput value={searchInput} onChange={setSearchInput} placeholder={t('a2a.searchPoliciesPlaceholder')} className="a2a-header-search py-0" />
        <select className="select !w-auto shrink-0 py-0" value={mode} onChange={(e) => { setMode(e.target.value as A2AAccessPolicyMode | ''); setPage(1); }}><option value="">{t('a2a.allModes')}</option><option value="allowlist">{t('a2a.modeAllowlist')}</option><option value="denylist">{t('a2a.modeDenylist')}</option></select>
        <select className="select !w-auto shrink-0 py-0" value={enabled} onChange={(e) => { setEnabled(e.target.value); setPage(1); }}><option value="">{t('a2a.allStatuses')}</option><option value="true">{t('common.enabled')}</option><option value="false">{t('common.disabled')}</option></select>
        <button className="btn sm shrink-0" onClick={() => void reload()}>{t('common.refresh')}</button><ResourceImportButton resourceType="a2a-policy" onImported={() => void reload()} className="btn sm shrink-0" /><button className="btn primary sm shrink-0" onClick={() => { setEditing(null); setModalOpen(true); }}>{t('a2a.newPolicy')}</button>
      </div></div>
      {tabs}
      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
        <div className="card !p-0">
          {loading ? (
            <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
          ) : error ? (
            <div className="p-4 text-sm text-danger">{error}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table w-max min-w-full">
                <thead>
                  <tr>
                    <th>{t('a2a.table.policy')}</th>
                    <th>{t('a2a.table.mode')}</th>
                    <th>{t('a2a.table.memberCount')}</th>
                    <th>{t('a2a.table.referenceCount')}</th>
                    <th>{t('a2a.table.status')}</th>
                    <th>{t('a2a.table.versionUpdated')}</th>
                    <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {!items.length ? (
                    <tr><td colSpan={7}><Empty text={t('a2a.emptyPolicies')} /></td></tr>
                  ) : items.map((row) => (
                    <tr key={row.policy_id}>
                      <td className="align-top">
                        <div className="text-text-strong font-medium break-words">{row.policy_name}</div>
                        <div className="text-[11px] text-muted">{truncate(row.description || '', 42) || '—'}</div>
                      </td>
                      <td className="whitespace-nowrap">{row.mode === 'allowlist' ? t('a2a.modeAllowlist') : t('a2a.modeDenylist')}</td>
                      <td className="whitespace-nowrap">{row.member_template_ids.length}</td>
                      <td className="whitespace-nowrap">
                        {row.reference_count > 0 ? (
                          <button type="button" className="tag cursor-pointer" title={t('a2a.policyReferenceTitle')} onClick={() => navigate(agentResourceReferencesPath('a2a-policy', row.policy_id, row.policy_name))}>{row.reference_count}</button>
                        ) : (
                          <span className="text-[11px] text-muted">0</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        <Switch checked={row.enabled} onChange={(value) => value || !row.reference_count ? void action(() => A2AAccessPolicyTemplateApi.update(row.policy_id, { enabled: value })) : setDisabling(row)} />
                      </td>
                      <td className="align-top">
                        <span className="badge">r{row.revision}</span>
                        <div className="mono text-[11px] text-muted">{formatTime(row.updated_at)}</div>
                      </td>
                      <td className="whitespace-nowrap min-w-[9.5rem]">
                        <div className="flex items-center gap-1">
                          <ResourceExportButton resourceType="a2a-policy" resourceIds={[row.policy_id]} />
                          <button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>{t('common.edit')}</button>
                          <button className="btn sm danger" onClick={() => row.reference_count ? toast('warn', t('a2a.policyReferencedNoDelete', { count: row.reference_count })) : setDeleting(row)}>{t('common.delete')}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {data && <Pagination page={page} pageSize={pageSize} total={data.total} onChange={(p, ps) => { setPage(p); setPageSize(ps); }} />}
      </div>
    </div>
    <A2AAccessPolicyModal open={modalOpen} policy={editing} onClose={() => setModalOpen(false)} onSaved={() => { setModalOpen(false); void reload(); }} />
    <ConfirmDialog open={!!deleting} message={t('a2a.deletePolicyConfirm')} danger onClose={() => setDeleting(null)} onConfirm={() => deleting && action(() => A2AAccessPolicyTemplateApi.remove(deleting.policy_id)).finally(() => setDeleting(null))} />
    <ConfirmDialog open={!!disabling} message={t('a2a.disablePolicyConfirm', { count: disabling?.reference_count ?? 0 })} danger onClose={() => setDisabling(null)} onConfirm={() => disabling && action(() => A2AAccessPolicyTemplateApi.update(disabling.policy_id, { enabled: false })).finally(() => setDisabling(null))} />
  </>;
}
