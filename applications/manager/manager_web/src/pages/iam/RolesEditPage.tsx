import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Pagination } from '../../components/Pagination';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import {
  ApiError,
  AuthzApi,
  AuthzRole,
  AuthzRoleUser,
  hasPermission,
  IamUser,
  PermissionDefinition,
  UserApi,
} from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';
import {
  isValidIdentityId,
  sanitizeIdentityIdInput,
} from '../../utils/identityId';

type Scope = 'admin' | 'org' | 'user';
type Section = 'basic' | 'permissions' | 'assignees';

type PermissionItem = Pick<
  PermissionDefinition,
  'permission_id' | 'name' | 'description' | 'resource_type' | 'scope'
>;

const RESOURCE_ORDER = ['quota', 'approval', 'iam'];

/** 与 authz_role 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  role_id: 64,
  name: 128,
  description: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max);
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.detail : (error as Error).message;
}

function FieldLabel({
  children,
  required = false,
  hint,
}: {
  children: ReactNode;
  required?: boolean;
  hint?: ReactNode;
}) {
  return (
    <label className="label">
      {children}
      {required && (
        <span className="text-danger ml-0.5" aria-hidden="true">*</span>
      )}
      {hint && (
        <span className="ml-1 text-xs font-normal text-muted">{hint}</span>
      )}
    </label>
  );
}

function SectionCard({
  title,
  hint,
  children,
  wide = false,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">{title}</div>
          {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
        </div>
      </div>
      <div className={wide ? 'space-y-3' : 'grid grid-cols-1 md:grid-cols-2 gap-3'}>
        {children}
      </div>
    </div>
  );
}

function groupPermissionItems(items: PermissionItem[]): Array<{
  resourceType: string;
  items: PermissionItem[];
}> {
  const groups = new Map<string, PermissionItem[]>();
  for (const permission of items) {
    const resourceType = permission.resource_type || 'other';
    groups.set(resourceType, [...(groups.get(resourceType) ?? []), permission]);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => {
      const leftIndex = RESOURCE_ORDER.indexOf(left);
      const rightIndex = RESOURCE_ORDER.indexOf(right);
      if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    })
    .map(([resourceType, groupItems]) => ({
      resourceType,
      items: [...groupItems].sort((left, right) =>
        left.permission_id.localeCompare(right.permission_id)),
    }));
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

export function RolesEditPage({ roleId }: { roleId?: string }) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { user } = useAuth();
  const isNew = !roleId;
  const canWrite = hasPermission(user, 'iam:role:write');

  const [section, setSection] = useState<Section>('basic');
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [roleMeta, setRoleMeta] = useState<AuthzRole | null>(null);

  const [formRoleId, setFormRoleId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scope, setScope] = useState<Scope>('admin');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [assignedIds, setAssignedIds] = useState<string[]>([]);
  const {
    searchInput: permissionSearchInput,
    setSearchInput: setPermissionSearchInput,
    searchQuery: permissionSearchQuery,
  } = useListSearch();

  const isSystem = !!roleMeta?.is_system;
  const { data: permissionData, loading: permissionsLoading } = useAsync(
    () =>
      AuthzApi.permissions({
        search: permissionSearchQuery,
        scope: isSystem ? undefined : scope,
        enabled: isSystem ? undefined : true,
      }),
    [permissionSearchQuery, scope, isSystem],
  );
  const permissions = permissionData?.items ?? [];

  useEffect(() => {
    if (isNew && !canWrite) {
      navigate('/roles');
    }
  }, [canWrite, isNew, navigate]);

  useEffect(() => {
    if (isNew || !roleId) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const role = await AuthzApi.getRole(roleId);
        if (cancelled) return;
        setRoleMeta(role);
        setFormRoleId(clipField(role.role_id, FIELD_MAX_LENGTH.role_id));
        setName(clipField(role.name, FIELD_MAX_LENGTH.name));
        setDescription(clipField(role.description ?? '', FIELD_MAX_LENGTH.description));
        setScope(role.scope);
        setSelected(new Set(role.permission_ids));
        setAssignedIds(role.user_ids);
      } catch (error) {
        if (cancelled) return;
        toast('danger', t('errors.loadFailed', { detail: errorMessage(error) }));
        navigate('/roles');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isNew, navigate, roleId, t]);

  const scopedPermissions = useMemo(
    () =>
      isSystem
        ? permissions.filter((permission) => selected.has(permission.permission_id))
        : permissions,
    [isSystem, permissions, selected],
  );
  const permissionGroups = useMemo(
    () => groupPermissionItems(scopedPermissions),
    [scopedPermissions],
  );

  const tabs = useMemo(() => {
    const items: Array<{ id: Section; label: string; count?: number }> = [
      { id: 'basic', label: t('roles.tabBasic') },
      {
        id: 'permissions',
        label: t('roles.tabPermissions'),
        count: selected.size,
      },
      {
        id: 'assignees',
        label: t('roles.tabAssignees'),
        count: isNew ? undefined : assignedIds.length,
      },
    ];
    return items;
  }, [assignedIds.length, isNew, selected.size, t]);

  const canSave = canWrite
    && !!name.trim()
    && (isNew ? !!formRoleId.trim() : true);

  const changeScope = (nextScope: Scope) => {
    setScope(nextScope);
    if (isSystem) return;
    void (async () => {
      try {
        const { items } = await AuthzApi.permissions({
          scope: nextScope,
          enabled: true,
        });
        const validIds = new Set(items.map((permission) => permission.permission_id));
        setSelected((current) => new Set(
          [...current].filter((id) => validIds.has(id)),
        ));
      } catch (error) {
        toast('danger', t('errors.loadFailed', { detail: errorMessage(error) }));
      }
    })();
  };

  const togglePermission = (permissionId: string) => {
    if (isSystem || !canWrite) return;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(permissionId)) next.delete(permissionId);
      else next.add(permissionId);
      return next;
    });
  };

  const submit = async () => {
    if (!canSave) {
      toast('warn', t('roles.fieldRequired', {
        field: !formRoleId.trim() ? t('roles.roleId') : t('roles.name'),
      }));
      setSection('basic');
      return;
    }
    if (isNew && !isValidIdentityId(formRoleId)) {
      toast('warn', t('roles.roleIdHint'));
      setSection('basic');
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        const created = await AuthzApi.createRole({
          role_id: formRoleId.trim(),
          name: name.trim().slice(0, FIELD_MAX_LENGTH.name),
          description: description.trim().slice(0, FIELD_MAX_LENGTH.description) || null,
          scope,
          permission_ids: [...selected],
        });
        toast('success', t('success.created'));
        navigate(`/roles/${encodeURIComponent(created.role_id)}`);
      } else if (roleId) {
        const updated = await AuthzApi.updateRole(roleId, {
          name: name.trim().slice(0, FIELD_MAX_LENGTH.name),
          description: description.trim().slice(0, FIELD_MAX_LENGTH.description) || null,
          scope,
          ...(isSystem ? {} : {
            permission_ids: [...selected],
          }),
        });
        setRoleMeta(updated);
        setAssignedIds(updated.user_ids);
        toast('success', t('success.saved'));
      }
    } catch (error) {
      toast('danger', t('errors.saveFailed', { detail: errorMessage(error) }));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  const pageTitle = isNew
    ? t('roles.new')
    : name.trim() || t('roles.edit');

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-x-auto">
      <div className="page-header flex w-full min-w-0 shrink-0 flex-col items-stretch gap-3 lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-center lg:gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 lg:justify-self-start">
          <button
            type="button"
            className="btn ghost sm shrink-0"
            onClick={() => navigate('/roles')}
            aria-label={t('roles.title')}
            title={t('roles.backToList')}
          >
            ←
          </button>
          <div className="min-w-0 max-w-full flex-1 sm:max-w-[32rem] sm:flex-none">
            <div className="page-title truncate" title={pageTitle}>
              {pageTitle}
            </div>
            <div className="text-[11px] text-muted mono truncate" title={formRoleId || undefined}>
              {formRoleId || '—'}
            </div>
          </div>
          {isSystem && (
            <span className="pill sm muted shrink-0" title={t('roles.systemHint')}>
              {t('roles.builtin')}
            </span>
          )}
        </div>

        <div className="tabs-bar max-w-full shrink-0 self-center overflow-x-auto lg:justify-self-center">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab ${section === tab.id ? 'active' : ''}`}
              onClick={() => setSection(tab.id)}
            >
              {tab.label}
              {tab.count != null && (
                <span className="tab__count">{tab.count}</span>
              )}
            </button>
          ))}
        </div>

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 lg:justify-self-end">
          {canWrite && (
            <button
              className="btn primary sm"
              disabled={saving || !canSave}
              onClick={() => void submit()}
            >
              {saving ? t('common.loading') : t('common.save')}
            </button>
          )}
        </div>
      </div>

      <div className="w-full min-w-0 shrink-0">
        {section === 'basic' && (
          <SectionCard
            title={t('roles.tabBasic')}
          >
            <div className="md:col-span-2">
              <FieldLabel
                required
                hint={
                  !isNew
                    ? t('roles.roleIdImmutableHint')
                    : t('roles.roleIdHint')
                }
              >
                {t('roles.roleId')}
              </FieldLabel>
              <LimitedTextInput
                className={`mono ${!isNew ? 'role-id-readonly' : ''}`}
                value={formRoleId}
                maxLength={FIELD_MAX_LENGTH.role_id}
                onChange={(value) => setFormRoleId(sanitizeIdentityIdInput(value))}
                disabled={!isNew || !canWrite}
                required
              />
            </div>
            <div className="md:col-span-2">
              <FieldLabel required>{t('roles.name')}</FieldLabel>
              <LimitedTextInput
                value={name}
                maxLength={FIELD_MAX_LENGTH.name}
                onChange={setName}
                disabled={!canWrite}
                required
              />
            </div>
            <div className="md:col-span-2">
              <FieldLabel>{t('iam.description')}</FieldLabel>
              <div className="flex items-start gap-2">
                <textarea
                  className="input w-full min-h-24 flex-1 min-w-0"
                  value={description}
                  onChange={(event) =>
                    setDescription(clipField(event.target.value, FIELD_MAX_LENGTH.description))
                  }
                  disabled={!canWrite}
                  maxLength={FIELD_MAX_LENGTH.description}
                />
                <span
                  className={`text-[11px] tabular-nums shrink-0 pt-2 ${
                    description.length >= FIELD_MAX_LENGTH.description
                      ? 'text-danger'
                      : 'text-muted'
                  }`}
                  aria-live="polite"
                >
                  {description.length}/{FIELD_MAX_LENGTH.description}
                </span>
              </div>
            </div>
            <div>
              <FieldLabel required>{t('roles.scope')}</FieldLabel>
              <select
                className="select w-full"
                value={scope}
                onChange={(event) => changeScope(event.target.value as Scope)}
                disabled={!canWrite || isSystem}
                required
              >
                <option value="admin">{t('roles.scopes.admin')}</option>
                <option value="org">{t('roles.scopes.org')}</option>
                <option value="user">{t('roles.scopes.user')}</option>
              </select>
            </div>
          </SectionCard>
        )}

        {section === 'permissions' && (
          <SectionCard
            title={t('roles.tabPermissions')}
            hint={isSystem ? t('roles.systemPermissionsLocked') : undefined}
            wide
          >
            <ListSearchInput
              value={permissionSearchInput}
              onChange={setPermissionSearchInput}
              placeholder={t('roles.searchPermissionPlaceholder')}
            />
            {permissionsLoading ? (
              <div className="text-sm text-muted">{t('common.loading')}</div>
            ) : scopedPermissions.length === 0 ? (
              <div className="text-sm text-muted">
                {permissionSearchQuery
                  ? t('common.empty')
                  : t('roles.noScopePermissions')}
              </div>
            ) : permissionGroups.map((group) => (
              <div key={group.resourceType} className="rounded-lg border border-border p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="text-sm font-semibold text-text-strong">
                    {permissionGroupLabel(t, group.resourceType)}
                  </div>
                  <span className="badge">{group.items.length}</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {group.items.map((permission) => {
                    const checked = selected.has(permission.permission_id);
                    const locked = isSystem || !canWrite;
                    return (
                      <label
                        key={permission.permission_id}
                        className={`rounded-lg border border-border bg-bg-accent/30 p-3 flex items-start gap-2 ${
                          locked ? '' : 'cursor-pointer hover:border-accent/40'
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={checked}
                          disabled={locked}
                          onChange={() => togglePermission(permission.permission_id)}
                        />
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">{permission.name}</span>
                          <span className="block mono text-[11px] text-muted break-all">
                            {permission.permission_id}
                          </span>
                          {permission.description && (
                            <span className="block text-xs text-muted mt-1">
                              {permission.description}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </SectionCard>
        )}

        {section === 'assignees' && (
          <AssigneesPanel
            roleId={roleId}
            canAssign={canWrite && !isNew}
            onAssignedIdsChange={setAssignedIds}
          />
        )}
      </div>
    </div>
  );
}

function AssigneesPanel({
  roleId,
  canAssign,
  onAssignedIdsChange,
}: {
  roleId?: string;
  canAssign: boolean;
  onAssignedIdsChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  type AssigneeSortField =
    | 'user_id'
    | 'granted_by'
    | 'expires_at'
    | 'created_at'
    | 'updated_at';

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [sortBy, setSortBy] = useState<AssigneeSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [removeTarget, setRemoveTarget] = useState<AuthzRoleUser | null>(null);
  const [editTarget, setEditTarget] = useState<AuthzRoleUser | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addExcludedIds, setAddExcludedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: AssigneeSortField, value: ColumnSortValue) => {
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
    () => {
      if (!roleId) return Promise.resolve(null);
      return AuthzApi.roleUsers(roleId, {
        page,
        page_size: pageSize,
        search: searchQuery,
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      });
    },
    [roleId, page, pageSize, searchQuery, sortBy, sortOrder],
  );
  const items = data?.items ?? [];

  const loadAllAssignmentUserIds = async (): Promise<string[]> => {
    if (!roleId) return [];
    const collected: string[] = [];
    let currentPage = 1;
    let total = 0;
    do {
      const batch = await AuthzApi.roleUsers(roleId, {
        page: currentPage,
        page_size: 200,
      });
      collected.push(...batch.items.map((item) => item.user_id));
      total = batch.total ?? collected.length;
      currentPage += 1;
    } while (collected.length < total);
    return collected;
  };

  if (!roleId) {
    return (
      <div className="card">
        <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted">
          {t('roles.saveBeforeAssign')}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div
              className="page-title truncate"
              title={t('roles.tabAssignees')}
            >
              {t('roles.tabAssignees')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('roles.assigneesSearchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
            {canAssign && (
              <button
                className="btn primary sm"
                onClick={() => {
                  void (async () => {
                    try {
                      const ids = await loadAllAssignmentUserIds();
                      setAddExcludedIds(ids);
                      setShowAdd(true);
                    } catch (loadError) {
                      toast('danger', t('errors.loadFailed', {
                        detail: errorMessage(loadError),
                      }));
                    }
                  })();
                }}
              >
                + {t('roles.addAssignee')}
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
                          label={t('iam.userId')}
                          value={sortBy === 'user_id' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('user_id', value)}
                        />
                      </th>
                      <th>
                        <TableColumnSort
                          label={t('roles.grantedBy')}
                          value={sortBy === 'granted_by' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('granted_by', value)}
                        />
                      </th>
                      <th>
                        <TableColumnSort
                          label={t('roles.expiresAt')}
                          value={sortBy === 'expires_at' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('expires_at', value)}
                        />
                      </th>
                      <th>
                        <TableColumnSort
                          label={t('roles.createdAt')}
                          value={sortBy === 'created_at' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('created_at', value)}
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
                      <th className="whitespace-nowrap min-w-[8rem]">
                        {t('common.actions')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.length === 0 ? (
                      <tr>
                        <td colSpan={6}>
                          <Empty text={t('roles.noAssignees')} />
                        </td>
                      </tr>
                    ) : items.map((row) => (
                      <tr key={row.user_id}>
                        <td className="mono text-[11px] text-muted break-all align-top">
                          {row.user_id}
                        </td>
                        <td className="mono text-[11px] text-muted break-all align-top">
                          {row.granted_by || '—'}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {row.expires_at
                            ? formatTime(row.expires_at)
                            : t('roles.neverExpires')}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(row.created_at)}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(row.updated_at)}
                        </td>
                        <td className="whitespace-nowrap min-w-[9.5rem]">
                          {canAssign ? (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                className="btn sm ghost"
                                disabled={busy}
                                onClick={() => setEditTarget(row)}
                              >
                                {t('common.edit')}
                              </button>
                              <button
                                type="button"
                                className="btn sm danger"
                                disabled={busy}
                                onClick={() => setRemoveTarget(row)}
                              >
                                {t('iam.removeMember')}
                              </button>
                            </div>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
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
              onChange={(nextPage, nextPageSize) => {
                setPage(nextPage);
                setPageSize(nextPageSize);
              }}
            />
          )}
        </div>
      </div>

      <ConfirmDialog
        open={!!removeTarget}
        message={t('roles.confirmRemoveAssignee', {
          userId: removeTarget?.user_id ?? '',
        })}
        danger
        onConfirm={async () => {
          if (!removeTarget || !roleId) return;
          setBusy(true);
          try {
            const currentIds = await loadAllAssignmentUserIds();
            const updated = await AuthzApi.replaceUsers(
              roleId,
              currentIds.filter((userId) => userId !== removeTarget.user_id),
            );
            onAssignedIdsChange(updated.user_ids);
            toast('success', t('success.saved'));
            void reload();
          } catch (updateError) {
            toast('danger', t('errors.saveFailed', {
              detail: errorMessage(updateError),
            }));
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setRemoveTarget(null)}
      />

      <AddAssigneeModal
        open={showAdd}
        assignedUserIds={addExcludedIds}
        onClose={() => setShowAdd(false)}
        onConfirm={async (userIds, expiresAt) => {
          setBusy(true);
          try {
            const updated = await AuthzApi.assignUsers(roleId, {
              user_ids: userIds,
              expires_at: expiresAt,
            });
            onAssignedIdsChange(updated.user_ids);
            toast('success', t('success.saved'));
            setShowAdd(false);
            void reload();
          } catch (updateError) {
            toast('danger', t('errors.saveFailed', {
              detail: errorMessage(updateError),
            }));
          } finally {
            setBusy(false);
          }
        }}
      />

      <EditAssigneeModal
        target={editTarget}
        onClose={() => setEditTarget(null)}
        onConfirm={async (userId, expiresAt) => {
          if (!roleId) return;
          setBusy(true);
          try {
            const updated = await AuthzApi.assignUsers(roleId, {
              user_ids: [userId],
              expires_at: expiresAt,
            });
            onAssignedIdsChange(updated.user_ids);
            toast('success', t('success.saved'));
            setEditTarget(null);
            void reload();
          } catch (updateError) {
            toast('danger', t('errors.saveFailed', {
              detail: errorMessage(updateError),
            }));
          } finally {
            setBusy(false);
          }
        }}
      />
    </>
  );
}

function toDatetimeLocalValue(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function parseExpiresAtInput(
  rawValue: string,
  t: ReturnType<typeof useTranslation>['t'],
): string | null | undefined {
  const raw = rawValue.trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    toast('warn', t('roles.expiresInvalid'));
    return undefined;
  }
  if (parsed.getTime() <= Date.now()) {
    toast('warn', t('roles.expiresMustBeFuture'));
    return undefined;
  }
  return parsed.toISOString();
}

function EditAssigneeModal({
  target,
  onClose,
  onConfirm,
}: {
  target: AuthzRoleUser | null;
  onClose: () => void;
  onConfirm: (userId: string, expiresAt: string | null) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [expiresAt, setExpiresAt] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (target) {
      setExpiresAt(toDatetimeLocalValue(target.expires_at));
      setSaving(false);
    } else {
      setExpiresAt('');
      setSaving(false);
    }
  }, [target]);

  const handleConfirm = async () => {
    if (!target) return;
    const nextExpiresAt = parseExpiresAtInput(expiresAt, t);
    if (nextExpiresAt === undefined) return;
    setSaving(true);
    try {
      await onConfirm(target.user_id, nextExpiresAt);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={!!target}
      title={t('roles.editAssignee')}
      onClose={onClose}
      footer={(
        <>
          <ModalCancelButton />
          <button
            type="button"
            className="btn primary"
            disabled={saving}
            onClick={() => void handleConfirm()}
          >
            {t('common.save')}
          </button>
        </>
      )}
    >
      <div className="space-y-3">
        <div>
          <span className="label">{t('iam.userId')}</span>
          <div className="mono mt-1 text-sm break-all text-text-strong">
            {target?.user_id ?? '—'}
          </div>
        </div>
        <label className="block">
          <span className="label">{t('roles.expiresAt')}</span>
          <input
            type="datetime-local"
            className="input mt-1 w-full cursor-pointer"
            value={expiresAt}
            onChange={(event) => setExpiresAt(event.target.value)}
            onClick={(event) => (event.target as HTMLInputElement).showPicker?.()}
          />
          <div className="text-[11px] text-muted mt-1">{t('roles.expiresHint')}</div>
        </label>
      </div>
    </Modal>
  );
}

function AddAssigneeModal({
  open,
  assignedUserIds,
  onClose,
  onConfirm,
}: {
  open: boolean;
  assignedUserIds: string[];
  onClose: () => void;
  onConfirm: (userIds: string[], expiresAt: string | null) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const excluded = useMemo(() => new Set(assignedUserIds), [assignedUserIds]);
  const { data, loading, error } = useAsync(
    () => {
      if (!open) return Promise.resolve(null);
      return UserApi.list({
        page: 1,
        page_size: 50,
        search: searchQuery,
        status: 'active',
      });
    },
    [searchQuery, open],
  );
  const candidates = useMemo(
    () => (data?.items ?? []).filter((user) => !excluded.has(user.user_id)),
    [data, excluded],
  );
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [expiresAt, setExpiresAt] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setSearchInput('');
      setExpiresAt('');
      setSelectedIds(new Set());
      setSaving(false);
    }
  }, [open, setSearchInput]);

  const toggleUser = (userId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const toggleAllVisible = () => {
    const visibleIds = candidates.map((user) => user.user_id);
    setSelectedIds((current) => {
      const next = new Set(current);
      const allSelected = visibleIds.every((id) => next.has(id));
      if (allSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  };

  const handleConfirm = async () => {
    if (selectedIds.size === 0) {
      toast('warn', t('roles.selectAssigneeRequired'));
      return;
    }
    const nextExpiresAt = parseExpiresAtInput(expiresAt, t);
    if (nextExpiresAt === undefined) return;
    setSaving(true);
    try {
      await onConfirm([...selectedIds], nextExpiresAt);
    } finally {
      setSaving(false);
    }
  };

  const allVisibleSelected = candidates.length > 0
    && candidates.every((user) => selectedIds.has(user.user_id));

  return (
    <Modal
      open={open}
      title={t('roles.addAssignee')}
      onClose={onClose}
      footer={(
        <>
          <ModalCancelButton />
          <button
            type="button"
            className="btn primary"
            disabled={saving || selectedIds.size === 0}
            onClick={() => void handleConfirm()}
          >
            {t('common.confirm')}
          </button>
        </>
      )}
    >
      <div className="space-y-3">
        <ListSearchInput
          value={searchInput}
          onChange={setSearchInput}
          placeholder={t('iam.searchUser')}
        />
        {error ? (
          <div className="text-sm text-danger">
            {t('errors.loadFailed', { detail: error })}
          </div>
        ) : loading ? (
          <div className="text-sm text-muted">{t('common.loading')}</div>
        ) : candidates.length === 0 ? (
          <div className="text-sm text-muted">{t('iam.noCandidates')}</div>
        ) : (
          <div className="rounded-lg border border-border">
            <label className="flex items-center gap-2 border-b border-border px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleAllVisible}
              />
              <span>{t('roles.selectAllVisible')}</span>
              <span className="text-xs text-muted">
                {t('roles.selectedCount', { count: selectedIds.size })}
              </span>
            </label>
            <div className="max-h-72 overflow-auto">
              {candidates.map((candidate: IamUser) => {
                const checked = selectedIds.has(candidate.user_id);
                return (
                  <label
                    key={candidate.user_id}
                    className="flex cursor-pointer items-start gap-2 border-b border-border px-3 py-2 last:border-b-0 hover:bg-bg-accent/30"
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={checked}
                      onChange={() => toggleUser(candidate.user_id)}
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {candidate.display_name}
                      </span>
                      <span className="mono block truncate text-xs text-muted">
                        {candidate.user_id}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        )}
        <label className="block">
          <span className="label">{t('roles.expiresAt')}</span>
          <input
            type="datetime-local"
            className="input mt-1 w-full cursor-pointer"
            value={expiresAt}
            onChange={(event) => setExpiresAt(event.target.value)}
            onClick={(event) => (event.target as HTMLInputElement).showPicker?.()}
          />
          <div className="text-[11px] text-muted mt-1">{t('roles.expiresHint')}</div>
        </label>
        <p className="text-[11px] text-muted">{t('roles.addAssigneeHint')}</p>
      </div>
    </Modal>
  );
}
