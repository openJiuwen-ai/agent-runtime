import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Pagination } from '../../components/Pagination';
import { Switch } from '../../components/Switch';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { useAsync } from '../../hooks/useAsync';
import { useGuideAutoOpen } from '../../hooks/useGuideAutoOpen';
import { useListSearch } from '../../hooks/useListSearch';
import { bumpGuideRevision } from '../../stores/guideStore';
import { ApiError, ContainerTemplateApi, ServiceConfigTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { ContainerTemplate } from '../../types';
import { formatTime, truncate } from '../../utils/format';
import { parseConfigSyncImport } from '../../utils/serviceConfigExport';
import { useRouter } from '../../router';
import { ContainerTemplateModal } from './ContainerTemplateModal';

type ContainerTemplateSortField = 'template_name' | 'container_id' | 'updated_at';

/**
 * 容器模板列表页：维护 AgentServer / Sandbox 容器的可复用运行规格，
 * 供运行时模板绑定（binding 关系见运行时模板编辑页）。
 */
export function ContainerTemplatesPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [enabledFilter, setEnabledFilter] = useState('');
  const [sortBy, setSortBy] = useState<ContainerTemplateSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const { searchInput, setSearchInput, searchQuery } = useListSearch();

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: ContainerTemplateSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  const { data, loading, error, reload } = useAsync(
    () =>
      ContainerTemplateApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder],
  );
  const [items, setItems] = useState<ContainerTemplate[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ContainerTemplate | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ContainerTemplate | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

  /** 与运行时模板列表的导入一致：实例池定义 JSON，含容器模板时一并落库 */
  const handleImportFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      void (async () => {
        setImporting(true);
        try {
          const text = String(reader.result ?? '');
          const parsed = JSON.parse(text) as unknown;
          const { body, templateCount } = parseConfigSyncImport(parsed);
          await ServiceConfigTemplateApi.create(body);
          toast(
            'success',
            templateCount > 1
              ? t('serviceConfigTemplate.importOkFirstOfMany', { count: templateCount })
              : t('serviceConfigTemplate.importOk'),
          );
          void reload();
          bumpGuideRevision();
        } catch (err) {
          toast(
            'danger',
            t('serviceConfigTemplate.importFailed', {
              detail:
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : String(err),
            }),
          );
        } finally {
          setImporting(false);
        }
      })();
    };
    reader.onerror = () => {
      toast('danger', t('serviceConfigTemplate.importFailed', { detail: 'read error' }));
    };
    reader.readAsText(file, 'UTF-8');
  };

  useEffect(() => setPage(1), [searchQuery]);
  useEffect(() => {
    if (data?.items) setItems(data.items);
  }, [data]);

  // 配置引导「新建容器模板」跳转后自动打开弹框
  useGuideAutoOpen('containerTemplateNew', () => {
    setEditing(null);
    setModalOpen(true);
  });

  const toggleEnabled = async (row: ContainerTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((current) =>
      current.map((item) => item.template_id === row.template_id ? { ...item, enabled } : item),
    );
    setTogglingId(row.template_id);
    try {
      await ContainerTemplateApi.update(row.template_id, { enabled });
      toast('success', t('success.saved'));
    } catch (toggleError) {
      setItems((current) =>
        current.map((item) =>
          item.template_id === row.template_id ? { ...item, enabled: previous } : item,
        ),
      );
      toast('danger', t('errors.saveFailed', {
        detail: toggleError instanceof ApiError ? toggleError.detail : (toggleError as Error).message,
      }));
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title">{t('containerTemplate.title')}</div>
            <div className="page-subtitle">{t('containerTemplate.subtitle')}</div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput value={searchInput} onChange={setSearchInput} placeholder={t('containerTemplate.searchPlaceholder')} className="basis-full sm:basis-auto" />
            <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
            <input
              ref={importInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={handleImportFile}
            />
            <button
              className="btn sm"
              disabled={importing}
              title={t('containerTemplate.importRuntimeAndContainerHint')}
              onClick={() => importInputRef.current?.click()}
            >
              {importing ? t('common.loading') : t('serviceConfigTemplate.import')}
            </button>
            <button className="btn primary sm" onClick={() => { setEditing(null); setModalOpen(true); }}>
              + {t('containerTemplate.new')}
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
                    <th>
                      <TableColumnSort
                        label={t('containerTemplate.templateName')}
                        value={sortBy === 'template_name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('template_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('containerTemplate.containerId')}
                        value={sortBy === 'container_id' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('container_id', value)}
                      />
                    </th>
                    <th>{t('containerTemplate.image')}</th>
                    <th>
                      <TableColumnFilter
                        label={t('common.enabled')}
                        value={enabledFilter}
                        options={[
                          { value: '', label: t('common.all') },
                          { value: 'true', label: t('common.enabled') },
                          { value: 'false', label: t('common.disabled') },
                        ]}
                        onChange={(value) => { setEnabledFilter(value); setPage(1); }}
                      />
                    </th>
                    <th>{t('containerTemplate.referenceCount')}</th>
                    <th>
                      <TableColumnSort
                        label={t('containerTemplate.updatedAt')}
                        value={sortBy === 'updated_at' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('updated_at', value)}
                      />
                    </th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr><td colSpan={7}><Empty text={t('containerTemplate.empty')} /></td></tr>
                  ) : items.map((row) => (
                    <tr key={row.template_id}>
                      <td className="align-top">
                        <div className="font-medium text-text-strong">{row.template_name}</div>
                        <div className="mono text-[11px] text-muted">{row.template_id}</div>
                      </td>
                      <td className="mono max-w-[14rem] break-all text-xs" title={row.container_id}>
                        {row.container_id}
                      </td>
                      <td className="mono max-w-[16rem] text-[11px] text-muted" title={row.image}>
                        {truncate(row.image, 36) || '—'}
                      </td>
                      <td>
                        <Switch checked={row.enabled} disabled={togglingId === row.template_id} onChange={(enabled) => void toggleEnabled(row, enabled)} />
                      </td>
                      <td>
                        {row.reference_count > 0 ? (
                          <span
                            className="tag cursor-pointer"
                            title={t('containerTemplate.referenceHint')}
                            onClick={() => navigate('/service-config-templates')}
                          >
                            {row.reference_count}
                          </span>
                        ) : (
                          <span className="text-[11px] text-muted">0</span>
                        )}
                      </td>
                      <td className="mono whitespace-nowrap text-[11px] text-muted">{formatTime(row.updated_at)}</td>
                      <td className="whitespace-nowrap">
                        <div className="flex gap-1">
                          <button className="btn sm ghost" onClick={() => { setEditing(row); setModalOpen(true); }}>{t('common.edit')}</button>
                          <button className="btn sm danger" onClick={() => setDeleteTarget(row)}>{t('common.delete')}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {data ? (
          <Pagination page={page} pageSize={pageSize} total={data.total ?? data.items.length} onChange={(nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); }} />
        ) : null}
      </div>
      <ContainerTemplateModal
        open={modalOpen}
        template={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => { setModalOpen(false); void reload(); bumpGuideRevision(); }}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        message={t('containerTemplate.deleteConfirm', { name: deleteTarget?.template_name ?? '' })}
        danger
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await ContainerTemplateApi.remove(deleteTarget.template_id);
            toast('success', t('success.deleted'));
            void reload();
            bumpGuideRevision();
          } catch (deleteError) {
            toast('danger', t('errors.deleteFailed', {
              detail: deleteError instanceof ApiError ? deleteError.detail : (deleteError as Error).message,
            }));
          }
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
