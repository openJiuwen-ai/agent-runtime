import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
  IamUser,
  isNoOrgGroupId,
  OrgApi,
  UserApi,
} from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';
import {
  isValidIdentityId,
  sanitizeIdentityIdInput,
} from '../../utils/identityId';

type Section = 'basic' | 'members';

/** 与 identity_org 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  group_id: 64,
  display_name: 128,
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
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">{title}</div>
          {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {children}
      </div>
    </div>
  );
}

export function OrgsEditPage({
  groupId,
  initialSection = 'basic',
}: {
  groupId?: string;
  initialSection?: Section;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const isNew = !groupId;
  const [section, setSection] = useState<Section>(initialSection);
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);

  const [formGroupId, setFormGroupId] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [memberCount, setMemberCount] = useState(0);

  const canWrite = true;

  useEffect(() => {
    setSection(initialSection);
  }, [initialSection]);

  useEffect(() => {
    if (isNew || !groupId) return;
    if (isNoOrgGroupId(groupId)) {
      navigate('/orgs');
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const org = await OrgApi.get(groupId);
        if (cancelled) return;
        if (isNoOrgGroupId(org.group_id)) {
          navigate('/orgs');
          return;
        }
        setFormGroupId(clipField(org.group_id, FIELD_MAX_LENGTH.group_id));
        setDisplayName(clipField(org.display_name ?? '', FIELD_MAX_LENGTH.display_name));
        try {
          const members = await OrgApi.listMembers(groupId);
          if (!cancelled) setMemberCount(members.users?.length ?? 0);
        } catch {
          if (!cancelled) setMemberCount(0);
        }
      } catch (error) {
        if (cancelled) return;
        toast('danger', t('errors.loadFailed', { detail: errorMessage(error) }));
        navigate('/orgs');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, isNew, navigate, t]);

  const tabs = useMemo(() => {
    const items: Array<{ id: Section; label: string; count?: number }> = [
      { id: 'basic', label: t('iam.tabBasic') },
      {
        id: 'members',
        label: t('iam.members'),
        count: isNew ? undefined : memberCount,
      },
    ];
    return items;
  }, [isNew, memberCount, t]);

  const canSave = canWrite && !!displayName.trim();

  const submit = async () => {
    if (!canWrite) return;
    if (!displayName.trim()) {
      toast('warn', t('iam.fieldRequired', { field: t('iam.displayName') }));
      setSection('basic');
      return;
    }
    if (isNew && formGroupId.trim() && !isValidIdentityId(formGroupId)) {
      toast('warn', t('iam.groupIdHint'));
      setSection('basic');
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        const created = await OrgApi.create({
          ...(formGroupId.trim() ? { group_id: formGroupId.trim() } : {}),
          display_name: displayName.trim().slice(0, FIELD_MAX_LENGTH.display_name),
        });
        toast('success', t('success.created'));
        navigate(`/orgs/${encodeURIComponent(created.group_id)}`);
      } else if (groupId) {
        await OrgApi.update(groupId, {
          display_name: displayName.trim().slice(0, FIELD_MAX_LENGTH.display_name),
        });
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
    ? t('iam.newOrg')
    : displayName.trim() || t('iam.editOrg');

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-x-auto">
      <div className="page-header flex w-full min-w-0 shrink-0 flex-col items-stretch gap-3 lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-center lg:gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 lg:justify-self-start">
          <button
            type="button"
            className="btn ghost sm shrink-0"
            onClick={() => navigate('/orgs')}
            aria-label={t('iam.orgs')}
            title={t('iam.backToOrgs')}
          >
            ←
          </button>
          <div className="min-w-0 max-w-full flex-1 sm:max-w-[32rem] sm:flex-none">
            <div className="page-title truncate" title={pageTitle}>
              {pageTitle}
            </div>
            <div className="text-[11px] text-muted mono truncate" title={formGroupId || undefined}>
              {formGroupId || '—'}
            </div>
          </div>
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
          {canWrite && section === 'basic' && (
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
          <SectionCard title={t('iam.tabBasic')}>
            <div className="md:col-span-2">
              <FieldLabel
                required={!isNew}
                hint={
                  !isNew
                    ? t('iam.groupIdImmutableHint')
                    : t('iam.groupIdHint')
                }
              >
                {t('iam.groupId')}
              </FieldLabel>
              <LimitedTextInput
                className={`mono ${!isNew ? 'role-id-readonly' : ''}`}
                value={formGroupId}
                maxLength={FIELD_MAX_LENGTH.group_id}
                onChange={(value) => setFormGroupId(sanitizeIdentityIdInput(value))}
                disabled={!isNew || !canWrite}
                required={!isNew}
              />
            </div>
            <div className="md:col-span-2">
              <FieldLabel required>{t('iam.displayName')}</FieldLabel>
              <LimitedTextInput
                value={displayName}
                maxLength={FIELD_MAX_LENGTH.display_name}
                onChange={setDisplayName}
                disabled={!canWrite}
                required
              />
            </div>
          </SectionCard>
        )}

        {section === 'members' && (
          <MembersPanel
            groupId={groupId}
            readOnly={isNew}
            isNew={isNew}
            onMemberCountChange={setMemberCount}
          />
        )}
      </div>
    </div>
  );
}

function MembersPanel({
  groupId,
  readOnly,
  isNew,
  onMemberCountChange,
}: {
  groupId?: string;
  readOnly: boolean;
  isNew: boolean;
  onMemberCountChange: (count: number) => void;
}) {
  const { t } = useTranslation();
  type MemberSortField = 'user_id' | 'display_name' | 'status' | 'updated_at';

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [sortBy, setSortBy] = useState<MemberSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [removeTarget, setRemoveTarget] = useState<IamUser | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [busy, setBusy] = useState(false);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: MemberSortField, value: ColumnSortValue) => {
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
      if (!groupId) return Promise.resolve(null);
      return OrgApi.listMembers(groupId);
    },
    [groupId],
  );

  const allMembers = data?.users ?? [];

  useEffect(() => {
    onMemberCountChange(allMembers.length);
  }, [allMembers.length, onMemberCountChange]);

  const filtered = useMemo(() => {
    const q = (searchQuery ?? '').trim().toLowerCase();
    let rows = allMembers;
    if (q) {
      rows = rows.filter(
        (user) =>
          user.user_id.toLowerCase().includes(q)
          || (user.display_name ?? '').toLowerCase().includes(q),
      );
    }
    if (sortBy) {
      const factor = sortOrder === 'desc' ? -1 : 1;
      rows = [...rows].sort((left, right) => {
        const lv = String(left[sortBy] ?? '');
        const rv = String(right[sortBy] ?? '');
        return lv.localeCompare(rv) * factor;
      });
    }
    return rows;
  }, [allMembers, searchQuery, sortBy, sortOrder]);

  const pageItems = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
    if (page > totalPages) setPage(totalPages);
  }, [filtered.length, page, pageSize]);

  if (isNew || !groupId) {
    return (
      <div className="card">
        <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted">
          {t('iam.saveBeforeMembers')}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title truncate" title={t('iam.members')}>
              {t('iam.members')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('iam.searchUser')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
            {!readOnly && (
              <button
                className="btn primary sm"
                onClick={() => setShowAdd(true)}
              >
                + {t('iam.addMember')}
              </button>
            )}
          </div>
        </div>

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
                        label={t('iam.displayName')}
                        value={sortBy === 'display_name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('display_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('common.enabled')}
                        value={sortBy === 'status' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('status', value)}
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
                    {!readOnly && (
                      <th className="whitespace-nowrap min-w-[8rem]">{t('common.actions')}</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {pageItems.length === 0 ? (
                    <tr>
                      <td colSpan={readOnly ? 4 : 5}>
                        <Empty text={t('iam.noMembers')} />
                      </td>
                    </tr>
                  ) : (
                    pageItems.map((user) => (
                      <tr key={user.user_id}>
                        <td className="mono text-[11px] text-muted break-all">
                          {user.user_id}
                        </td>
                        <td className="text-text-strong font-medium break-words">
                          {user.display_name || '—'}
                        </td>
                        <td className="whitespace-nowrap">
                          {user.status === 'active'
                            ? t('common.enabled')
                            : t('common.disabled')}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(user.updated_at)}
                        </td>
                        {!readOnly && (
                          <td className="whitespace-nowrap">
                            <button
                              className="btn sm danger"
                              disabled={busy}
                              onClick={() => setRemoveTarget(user)}
                            >
                              {t('iam.removeMember')}
                            </button>
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <Pagination
          page={page}
          pageSize={pageSize}
          total={filtered.length}
          loading={loading}
          error={error}
          onChange={(nextPage, nextPageSize) => {
            setPage(nextPage);
            setPageSize(nextPageSize);
          }}
        />
      </div>

      <AddMembersModal
        open={showAdd}
        assignedUserIds={allMembers.map((user) => user.user_id)}
        onClose={() => setShowAdd(false)}
        onConfirm={async (userIds) => {
          setBusy(true);
          try {
            await OrgApi.addMembers(groupId, userIds);
            toast('success', t('success.saved'));
            setShowAdd(false);
            await reload();
          } catch (error) {
            toast('danger', t('errors.saveFailed', { detail: errorMessage(error) }));
          } finally {
            setBusy(false);
          }
        }}
      />

      <ConfirmDialog
        open={!!removeTarget}
        danger
        message={t('iam.confirmRemoveMember', {
          name: removeTarget?.display_name || removeTarget?.user_id || '',
        })}
        onClose={() => setRemoveTarget(null)}
        onConfirm={async () => {
          if (!removeTarget) return;
          setBusy(true);
          try {
            await OrgApi.removeMember(groupId, removeTarget.user_id);
            toast('success', t('success.deleted'));
            setRemoveTarget(null);
            await reload();
          } catch (error) {
            toast('danger', t('errors.deleteFailed', { detail: errorMessage(error) }));
          } finally {
            setBusy(false);
          }
        }}
      />
    </>
  );
}

function AddMembersModal({
  open,
  assignedUserIds,
  onClose,
  onConfirm,
}: {
  open: boolean;
  assignedUserIds: string[];
  onClose: () => void;
  onConfirm: (userIds: string[]) => Promise<void>;
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
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setSearchInput('');
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
      toast('warn', t('iam.selectMemberRequired'));
      return;
    }
    setSaving(true);
    try {
      await onConfirm([...selectedIds]);
    } finally {
      setSaving(false);
    }
  };

  const allVisibleSelected = candidates.length > 0
    && candidates.every((user) => selectedIds.has(user.user_id));

  return (
    <Modal
      open={open}
      title={t('iam.addMember')}
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
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={allVisibleSelected}
            disabled={candidates.length === 0}
            onChange={toggleAllVisible}
          />
          <span>{t('roles.selectAllVisible')}</span>
          {selectedIds.size > 0 && (
            <span className="text-muted">
              {t('roles.selectedCount', { count: selectedIds.size })}
            </span>
          )}
        </label>
        <div className="max-h-64 overflow-auto rounded-md border border-border p-2">
          {loading ? (
            <div className="text-sm text-muted">{t('common.loading')}</div>
          ) : error ? (
            <div className="text-sm text-danger">{error}</div>
          ) : candidates.length === 0 ? (
            <div className="text-sm text-muted">{t('iam.noCandidates')}</div>
          ) : (
            candidates.map((user) => (
              <label
                key={user.user_id}
                className="flex items-center gap-2 py-1.5 text-sm"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(user.user_id)}
                  onChange={() => toggleUser(user.user_id)}
                />
                <span className="min-w-0 flex-1 truncate">
                  {user.display_name || user.user_id}
                  <span className="ml-2 text-[11px] text-muted mono">{user.user_id}</span>
                </span>
              </label>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}
