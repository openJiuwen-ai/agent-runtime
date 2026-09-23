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
import { useAuth } from '../../auth/AuthContext';
import {
  ApiError,
  AuthzApi,
  AuthzRole,
  hasPermission,
  PermissionDefinition,
} from '../../services/api';
import { useRouter } from '../../router';
import { toast } from '../../stores/uiStore';
import { formatTime, truncate } from '../../utils/format';

type RoleSortField = 'name' | 'role_id' | 'description' | 'updated_at';

const RESOURCE_ORDER = ['quota', 'approval', 'iam'];

function permissionCountByGroup(
  permissionIds: string[],
  permissions: PermissionDefinition[],
): Array<{ resourceType: string; count: number }> {
  const byId = new Map(permissions.map((p) => [p.permission_id, p]));
  const counts = new Map<string, number>();
  for (const permissionId of permissionIds) {
    const permission = byId.get(permissionId);
    const resourceType = permission?.resource_type
      || permissionId.split(':')[0]
      || 'other';
    counts.set(resourceType, (counts.get(resourceType) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(([left], [right]) => {
      const leftIndex = RESOURCE_ORDER.indexOf(left);
      const rightIndex = RESOURCE_ORDER.indexOf(right);
      if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    })
    .map(([resourceType, count]) => ({ resourceType, count }));
}

function permissionGroupLabel(
  t: ReturnType<typeof useTranslation>['t'],
  resourceType: string,
): string {
  if (RESOURCE_ORDER.includes(resourceType)) {
    return t(`roles.permissionGroups.${resourceType}`);
  }
  return t('roles.permissionGroups.other');
}

export function RolesPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { user } = useAuth();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState<string>('');
  const [scopeFilter, setScopeFilter] = useState<string>('');
  const [sortBy, setSortBy] = useState<RoleSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [items, setItems] = useState<AuthzRole[]>([]);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [delTarget, setDelTarget] = useState<AuthzRole | null>(null);

  const canWrite = hasPermission(user, 'iam:role:write');
  const canOpenEditor = canWrite || hasPermission(user, 'iam:role:read');

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: RoleSortField, value: ColumnSortValue) => {
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
      AuthzApi.roles({
        page,
        page_size: pageSize,
        search: searchQuery,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        scope: scopeFilter === ''
          ? undefined
          : (scopeFilter as 'admin' | 'org' | 'user'),
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, enabledFilter, scopeFilter, sortBy, sortOrder],
  );
  const { data: permissionData } = useAsync(() => AuthzApi.permissions(), []);
  const permissions = permissionData?.items ?? [];

  useEffect(() => {
    if (data?.items) {
      setItems(data.items);
    }
  }, [data]);

  const toggleEnabled = async (row: AuthzRole, enabled: boolean) => {
    if (togglingId || row.is_system || !canWrite) return;
    const previous = row.enabled;
    setItems((list) =>
      list.map((item) => (item.role_id === row.role_id ? { ...item, enabled } : item)),
    );
    setTogglingId(row.role_id);
    try {
      await AuthzApi.updateRole(row.role_id, { enabled });
      if (enabledFilter !== '' && enabled !== (enabledFilter === 'true')) {
        setItems((list) => list.filter((item) => item.role_id !== row.role_id));
      }
      toast('success', t('success.saved'));
    } catch (e) {
      setItems((list) =>
        list.map((item) =>
          (item.role_id === row.role_id ? { ...item, enabled: previous } : item)),
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
        <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
          <div className="page-title truncate" title={t('roles.title')}>
            {t('roles.title')}
          </div>
          <div className="page-subtitle truncate" title={t('roles.subtitle')}>
            {t('roles.subtitle')}
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('roles.searchPlaceholder')}
            className="basis-full sm:basis-auto"
          />
          <button className="btn sm" onClick={() => void reload()}>
            {t('common.refresh')}
          </button>
          {canWrite && (
            <button
              className="btn primary sm"
              onClick={() => navigate('/roles/new')}
            >
              + {t('roles.new')}
            </button>
          )}
        </div>
      </div>

      <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
        <div className="card !p-0">
          {loading ? (
            <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
          ) : error ? (
            <div className="p-4 text-sm text-danger">
              {t('errors.loadFailed', { detail: error })}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="table w-max min-w-full">
                <thead>
                  <tr>
                    <th>
                      <TableColumnSort
                        label={t('roles.roleId')}
                        value={sortBy === 'role_id' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('role_id', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('roles.name')}
                        value={sortBy === 'name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('roles.description')}
                        value={sortBy === 'description' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('description', value)}
                      />
                    </th>
                    <th>
                      <TableColumnFilter
                        label={t('roles.scope')}
                        value={scopeFilter}
                        options={[
                          { value: '', label: t('common.all') },
                          { value: 'admin', label: t('roles.scopes.admin') },
                          { value: 'org', label: t('roles.scopes.org') },
                          { value: 'user', label: t('roles.scopes.user') },
                        ]}
                        onChange={(value) => {
                          setScopeFilter(value);
                          setPage(1);
                        }}
                      />
                    </th>
                    <th>{t('roles.permissions')}</th>
                    <th>{t('roles.assignees')}</th>
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
                    <th>
                      <TableColumnSort
                        label={t('common.updatedAt')}
                        value={sortBy === 'updated_at' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('updated_at', value)}
                      />
                    </th>
                    <th className="whitespace-nowrap min-w-[8rem]">{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr>
                      <td colSpan={9}>
                        <Empty text={t('common.empty')} />
                      </td>
                    </tr>
                  ) : items.map((role) => {
                    const groups = permissionCountByGroup(
                      role.permission_ids,
                      permissions,
                    );
                    return (
                      <tr key={role.role_id}>
                        <td className="mono text-[11px] text-muted break-all align-top">
                          {role.role_id}
                        </td>
                        <td className="align-top">
                          <div className="font-medium text-text-strong break-words">
                            {role.name}
                          </div>
                        </td>
                        <td
                          className="text-[11px] text-muted max-w-[14rem]"
                          title={role.description ?? undefined}
                        >
                          {role.description ? truncate(role.description, 48) : '—'}
                        </td>
                        <td>
                          <div className="flex flex-wrap items-center gap-1">
                            <span className="badge">
                              {t(`roles.scopes.${role.scope}`)}
                            </span>
                            {role.is_system && (
                              <span className="pill sm muted" title={t('roles.systemHint')}>
                                {t('roles.builtin')}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="max-w-[20rem]">
                          {groups.length === 0 ? (
                            <span className="text-muted">{t('roles.noPermissions')}</span>
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {groups.map((group) => (
                                <span key={group.resourceType} className="badge">
                                  {permissionGroupLabel(t, group.resourceType)} · {group.count}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td>{role.assignee_count}</td>
                        <td className="whitespace-nowrap">
                          <Switch
                            checked={role.enabled}
                            disabled={
                              !canWrite
                              || role.is_system
                              || togglingId === role.role_id
                            }
                            aria-label={
                              role.enabled ? t('common.enabled') : t('common.disabled')
                            }
                            onChange={(enabled) => void toggleEnabled(role, enabled)}
                          />
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(role.updated_at)}
                        </td>
                        <td className="whitespace-nowrap min-w-[9.5rem]">
                          <div className="flex items-center gap-1">
                            {canOpenEditor && (
                              <button
                                className="btn sm ghost"
                                onClick={() =>
                                  navigate(`/roles/${encodeURIComponent(role.role_id)}`)
                                }
                              >
                                {t('common.edit')}
                              </button>
                            )}
                            {canWrite && !role.is_system && (
                              <button
                                className="btn sm danger"
                                onClick={() => setDelTarget(role)}
                              >
                                {t('common.delete')}
                              </button>
                            )}
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
      message={t('roles.confirmDelete', {
        name: delTarget?.name ?? '',
        id: delTarget?.role_id ?? '',
      })}
      danger
      onConfirm={async () => {
        if (!delTarget) return;
        try {
          await AuthzApi.deleteRole(delTarget.role_id);
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
