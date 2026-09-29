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
import { ApiError, Org, OrgApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';
import { ResourceExportButton, ResourceImportButton } from '../../components/ResourceImportExport';

type OrgSortField = 'group_id' | 'display_name' | 'status' | 'updated_at';

export function OrgsPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [statusFilter, setStatusFilter] = useState('');
  const [sortBy, setSortBy] = useState<OrgSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [delTarget, setDelTarget] = useState<Org | null>(null);
  const [items, setItems] = useState<Org[]>([]);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());

  const currentIds = items.map((item) => item.group_id);
  const allCurrentSelected = currentIds.length > 0 && currentIds.every((id) => selectedIds.has(id));
  const toggleCurrentPage = (checked: boolean) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      for (const id of currentIds) checked ? next.add(id) : next.delete(id);
      return next;
    });
  };

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: OrgSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy('');
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const { data, loading, error, reload } = useAsync(
    () =>
      OrgApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        status: statusFilter || undefined,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, statusFilter, sortBy, sortOrder],
  );

  useEffect(() => {
    if (data?.items) setItems(data.items);
  }, [data]);

  const toggleStatus = async (row: Org, enabled: boolean) => {
    if (togglingId) return;
    const nextStatus = enabled ? 'active' : 'disabled';
    const previous = row.status;
    setItems((list) =>
      list.map((item) => (item.group_id === row.group_id ? { ...item, status: nextStatus } : item)),
    );
    setTogglingId(row.group_id);
    try {
      await OrgApi.update(row.group_id, { status: nextStatus });
      if (statusFilter !== '' && nextStatus !== statusFilter) {
        setItems((list) => list.filter((item) => item.group_id !== row.group_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) =>
          (item.group_id === row.group_id ? { ...item, status: previous } : item)),
      );
      toast(
        'danger',
        t('errors.saveFailed', {
          detail: e instanceof ApiError ? e.detail : (e as Error).message,
        }),
      );
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('iam.orgs')}>
              {t('iam.orgs')}
            </div>
            <div className="page-subtitle truncate" title={t('iam.orgsSubtitle')}>
              {t('iam.orgsSubtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('iam.orgsSearchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
            <ResourceImportButton resourceType="organization" onImported={() => void reload()} />
            <ResourceExportButton
              resourceType="organization"
              resourceIds={[...selectedIds]}
              disabled={selectedIds.size === 0}
              className="btn sm"
            />
            {selectedIds.size > 0 && (
              <span className="text-xs text-muted">{t('importExport.selected', { count: selectedIds.size })}</span>
            )}
            <button className="btn primary sm" onClick={() => navigate('/orgs/new')}>
              + {t('iam.newOrg')}
            </button>
          </div>
        </div>

        <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
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
                      <th className="w-10">
                        <input
                          type="checkbox"
                          aria-label={t('importExport.export')}
                          checked={allCurrentSelected}
                          onChange={(event) => toggleCurrentPage(event.target.checked)}
                        />
                      </th>
                      <th className="w-[25rem] max-w-[25rem]">
                        <TableColumnSort
                          label={t('iam.groupId')}
                          value={sortBy === 'group_id' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('group_id', value)}
                        />
                      </th>
                      <th>
                        <TableColumnSort
                          label={t('iam.displayName')}
                          value={sortBy === 'display_name' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('display_name', value)}
                        />
                      </th>
                      <th>
                        <div className="th-filter">
                          <span className="th-filter__label">{t('common.enabled')}</span>
                          <TableColumnSort
                            iconOnly
                            label={t('common.enabled')}
                            value={sortBy === 'status' ? sortOrder : ''}
                            options={sortOptions}
                            onChange={(value) => handleSortChange('status', value)}
                          />
                          <TableColumnFilter
                            iconOnly
                            label={t('common.enabled')}
                            value={statusFilter}
                            options={[
                              { value: '', label: t('common.all') },
                              { value: 'active', label: t('common.enabled') },
                              { value: 'disabled', label: t('common.disabled') },
                            ]}
                            onChange={(value) => {
                              setStatusFilter(value);
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
                      <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.length === 0 ? (
                      <tr>
                        <td colSpan={6}>
                          <Empty text={t('common.empty')} />
                        </td>
                      </tr>
                    ) : (
                      items.map((o) => (
                        <tr key={o.group_id}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={o.group_id}
                              checked={selectedIds.has(o.group_id)}
                              onChange={(event) => setSelectedIds((previous) => {
                                const next = new Set(previous);
                                event.target.checked ? next.add(o.group_id) : next.delete(o.group_id);
                                return next;
                              })}
                            />
                          </td>
                          <td
                            className="mono text-[11px] text-muted w-[25rem] max-w-[25rem] break-all"
                            title={o.group_id}
                          >
                            {o.group_id}
                          </td>
                          <td className="text-text-strong font-medium break-words">{o.display_name}</td>
                          <td className="whitespace-nowrap">
                            <Switch
                              checked={o.status === 'active'}
                              disabled={togglingId === o.group_id}
                              aria-label={
                                o.status === 'active' ? t('common.enabled') : t('common.disabled')
                              }
                              onChange={(enabled) => void toggleStatus(o, enabled)}
                            />
                          </td>
                          <td className="mono text-[11px] text-muted whitespace-nowrap">
                            {formatTime(o.updated_at)}
                          </td>
                          <td className="whitespace-nowrap min-w-[9.5rem]">
                            <div className="flex items-center gap-1">
                              <button
                                className="btn sm ghost"
                                onClick={() =>
                                  navigate(`/orgs/${encodeURIComponent(o.group_id)}`)
                                }
                              >
                                {t('common.edit')}
                              </button>
                              <button className="btn sm danger" onClick={() => setDelTarget(o)}>
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
              total={data.total ?? data.items.length}
              onChange={(p, ps) => {
                setPage(p);
                setPageSize(ps);
              }}
            />
          )}
        </div>
      </div>

      <ConfirmDialog
        open={!!delTarget}
        message={t('iam.confirmDeleteOrg', { name: delTarget?.display_name ?? '' })}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await OrgApi.remove(delTarget.group_id);
            toast('success', t('success.deleted'));
            void reload();
          } catch (e) {
            toast(
              'danger',
              t('errors.deleteFailed', {
                detail: e instanceof ApiError ? e.detail : (e as Error).message,
              }),
            );
          }
        }}
        onClose={() => setDelTarget(null)}
      />
    </>
  );
}
