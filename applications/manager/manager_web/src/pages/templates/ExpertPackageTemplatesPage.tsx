import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { ExpertPackageTemplateApi, ApiError } from '../../services/api';
import type { ExpertPackageTemplate } from '../../types';
import { Pagination } from '../../components/Pagination';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Switch } from '../../components/Switch';
import { ListSearchInput } from '../../components/ListSearchInput';
import { ExpertPackageTemplateModal } from './ExpertPackageTemplateModal';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';
import { useRouter } from '../../router';
import { ResourceExportButton, ResourceImportButton } from '../../components/ResourceImportExport';
import { agentResourceReferencesPath } from '../../utils/agentTemplateNavigation';
import { expertResourceReturnPath } from '../../utils/expertResourceNavigation';

export function ExpertPackageTemplatesPage() {
  const { t, i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const returnPath = expertResourceReturnPath(params.get('returnTo'));
  const zh = i18n.language.startsWith('zh');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ExpertPackageTemplate | null>(null);
  const [delTarget, setDelTarget] = useState<ExpertPackageTemplate | null>(null);
  const [items, setItems] = useState<ExpertPackageTemplate[]>([]);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const { data, loading, error, reload } = useAsync(
    () =>
      ExpertPackageTemplateApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
      }),
    [page, pageSize, searchQuery],
  );

  useEffect(() => {
    if (data?.items) setItems(data.items);
  }, [data]);

  const toggleEnabled = async (row: ExpertPackageTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await ExpertPackageTemplateApi.update(row.template_id, { enabled });
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) =>
          item.template_id === row.template_id ? { ...item, enabled: previous } : item,
        ),
      );
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        {returnPath && <div><button type="button" className="btn" onClick={() => navigate(returnPath)}>
          {returnPath.startsWith('/instances/')
            ? (zh ? '← 返回来源集群的 Agent绑定' : '← Back to source cluster Agent bindings')
            : (zh ? '← 返回 Agent模板' : '← Back to Agent template')}
        </button></div>}
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title truncate" title={t('expertPackageTemplate.title')}>
              {t('expertPackageTemplate.title')}
            </div>
            <div className="page-subtitle truncate" title={t('expertPackageTemplate.subtitle')}>
              {t('expertPackageTemplate.subtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('expertPackageTemplate.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
            <ResourceImportButton resourceType="expert-package" onImported={() => void reload()} />
            <button
              className="btn primary sm"
              onClick={() => {
                setEditing(null);
                setModalOpen(true);
              }}
            >
              + {t('expertPackageTemplate.new')}
            </button>
          </div>
        </div>

        <div className="card !p-0">
          {loading ? (
            <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
          ) : error ? (
            <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table w-max min-w-full">
                <thead>
                  <tr>
                    <th>{t('expertPackageTemplate.templateName')}</th>
                    <th>{t('expertPackageTemplate.templateDescription')}</th>
                    <th>{t('expertPackageTemplate.packageSummaryLabel')}</th>
                    <th>{t('common.enabled')}</th>
                    <th>{t('expertPackageTemplate.referenceCount')}</th>
                    <th>{t('expertPackageTemplate.updatedAt')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="p-4 text-sm text-muted">
                        {t('common.empty')}
                      </td>
                    </tr>
                  ) : (
                    items.map((row) => (
                      <tr key={row.template_id}>
                        <td className="align-top">
                          <div className="font-medium break-words">{row.template_name}</div>
                          <div className="mono text-[11px] text-muted break-all">{row.template_id}</div>
                        </td>
                        <td className="max-w-[14rem] text-[11px] text-muted" title={row.description ?? undefined}>
                          {row.description ? truncate(row.description, 48) : '—'}
                        </td>
                        <td className="whitespace-nowrap text-sm">
                          {t('expertPackageTemplate.packageSummary', {
                            experts: (row.packages ?? []).filter((item) => item.kind === 'agent_template').length,
                            groups: (row.packages ?? []).filter((item) => item.kind === 'agent_group').length,
                          })}
                        </td>
                        <td>
                          <Switch
                            checked={row.enabled}
                            disabled={togglingId === row.template_id}
                            aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                            onChange={(enabled) => void toggleEnabled(row, enabled)}
                          />
                        </td>
                        <td>
                          {row.reference_count > 0 ? (
                            <span
                              className="tag cursor-pointer"
                              title={t('expertPackageTemplate.referenceHint')}
                              onClick={() => navigate(agentResourceReferencesPath('expert-package', row.template_id, row.template_name))}
                            >
                              {row.reference_count}
                            </span>
                          ) : (
                            <span className="text-[11px] text-muted">0</span>
                          )}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(row.updated_at)}
                        </td>
                        <td className="whitespace-nowrap">
                          <div className="flex items-center gap-1">
                            <ResourceExportButton resourceType="expert-package" resourceIds={[row.template_id]} />
                            <button
                              className="btn sm ghost"
                              onClick={() => {
                                setEditing(row);
                                setModalOpen(true);
                              }}
                            >
                              {t('common.edit')}
                            </button>
                            <button className="btn sm danger" onClick={() => setDelTarget(row)}>
                              {t('common.delete')}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {data && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data.total ?? items.length}
            onChange={(nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            }}
          />
        )}
      </div>

      <ExpertPackageTemplateModal
        open={modalOpen}
        template={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => {
          setModalOpen(false);
          void reload();
        }}
      />
      <ConfirmDialog
        open={!!delTarget}
        message={t('expertPackageTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await ExpertPackageTemplateApi.remove(delTarget.template_id);
            toast('success', t('success.deleted'));
            void reload();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', {
              detail: e instanceof ApiError ? e.detail : (e as Error).message,
            }));
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </>
  );
}
