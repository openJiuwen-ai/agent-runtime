import { useEffect, useMemo, useState } from 'react';
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
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { ApiError, McpTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { McpTemplate } from '../../types';
import { formatTime, truncate } from '../../utils/format';
import { McpTemplateModal } from './McpTemplateModal';

type McpTemplateSortField = 'template_name' | 'description' | 'updated_at';

function entryUrl(row: McpTemplate): string {
  return String(row.mcp_entry?.url ?? '').trim();
}

function entryName(row: McpTemplate): string {
  return String(row.mcp_entry?.name ?? '').trim();
}

function entryTransport(row: McpTemplate): string {
  return String(row.mcp_entry?.transport ?? '').trim();
}

export function McpTemplatesPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [enabledFilter, setEnabledFilter] = useState('');
  const [sortBy, setSortBy] = useState<McpTemplateSortField | ''>('');
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

  const handleSortChange = (field: McpTemplateSortField, value: ColumnSortValue) => {
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
      McpTemplateApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, sortBy, sortOrder],
  );
  const [items, setItems] = useState<McpTemplate[]>([]);
  const [editing, setEditing] = useState<McpTemplate | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<McpTemplate | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  useEffect(() => setPage(1), [searchQuery]);
  useEffect(() => {
    if (data?.items) setItems(data.items);
  }, [data]);

  const toggleEnabled = async (row: McpTemplate, enabled: boolean) => {
    if (togglingId) return;
    const previous = row.enabled;
    setItems((current) =>
      current.map((item) => (item.template_id === row.template_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.template_id);
    try {
      await McpTemplateApi.update(row.template_id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.template_id !== row.template_id));
      }
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
            <div className="page-title truncate">{t('mcpTemplate.title')}</div>
            <div className="page-subtitle truncate">{t('mcpTemplate.subtitle')}</div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('mcpTemplate.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
            <button
              className="btn primary sm"
              onClick={() => {
                setEditing(null);
                setModalOpen(true);
              }}
            >
              + {t('mcpTemplate.new')}
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
                        label={t('mcpTemplate.templateName')}
                        value={sortBy === 'template_name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('template_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('mcpTemplate.templateDescription')}
                        value={sortBy === 'description' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('description', value)}
                      />
                    </th>
                    <th>{t('mcpTemplate.mcpName')}</th>
                    <th>{t('mcpTemplate.transport')}</th>
                    <th>{t('mcpTemplate.url')}</th>
                    <th>
                      <TableColumnFilter
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
                    </th>
                    <th>{t('mcpTemplate.referenceCount')}</th>
                    <th>
                      <TableColumnSort
                        label={t('mcpTemplate.updatedAt')}
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
                    <tr><td colSpan={9}><Empty text={t('common.empty')} /></td></tr>
                  ) : items.map((row) => (
                    <tr key={row.template_id}>
                      <td className="align-top">
                        <div className="font-medium text-text-strong">{row.template_name}</div>
                        <div className="mono text-[11px] text-muted">{row.template_id}</div>
                      </td>
                      <td className="max-w-[14rem] text-[11px] text-muted" title={row.description ?? undefined}>
                        {row.description ? truncate(row.description, 48) : '—'}
                      </td>
                      <td className="mono max-w-[12rem] break-all text-xs">{entryName(row) || '—'}</td>
                      <td className="whitespace-nowrap">
                        {entryTransport(row) ? <span className="tag">{entryTransport(row)}</span> : '—'}
                      </td>
                      <td className="mono max-w-[16rem] text-[11px] text-muted" title={entryUrl(row)}>
                        {entryUrl(row) ? truncate(entryUrl(row), 40) : '—'}
                      </td>
                      <td>
                        <Switch
                          checked={row.enabled}
                          disabled={togglingId === row.template_id}
                          onChange={(enabled) => void toggleEnabled(row, enabled)}
                        />
                      </td>
                      <td className="whitespace-nowrap">
                        {row.reference_count > 0 ? (
                          <span
                            className="tag cursor-pointer"
                            title={t('mcpTemplate.referenceHint')}
                            onClick={() => navigate('/agent-templates')}
                          >
                            {row.reference_count}
                          </span>
                        ) : (
                          <span className="text-[11px] text-muted">0</span>
                        )}
                      </td>
                      <td className="mono whitespace-nowrap text-[11px] text-muted">
                        {formatTime(row.updated_at)}
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="flex gap-1">
                          <button
                            className="btn sm ghost"
                            onClick={() => {
                              setEditing(row);
                              setModalOpen(true);
                            }}
                          >
                            {t('common.edit')}
                          </button>
                          <button className="btn sm danger" onClick={() => setDeleteTarget(row)}>
                            {t('common.delete')}
                          </button>
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
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data.total ?? data.items.length}
            onChange={(nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            }}
          />
        ) : null}
      </div>
      <McpTemplateModal
        open={modalOpen}
        template={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => {
          setModalOpen(false);
          void reload();
        }}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        message={t('mcpTemplate.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await McpTemplateApi.remove(deleteTarget.template_id);
            toast('success', t('success.deleted'));
            void reload();
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
