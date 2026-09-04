import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Pagination } from '../../components/Pagination';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { useAsync } from '../../hooks/useAsync';
import { useFormDirty } from '../../hooks/useFormDirty';
import { useListSearch } from '../../hooks/useListSearch';
import { ApiError, IamUser, NO_ORG_GROUP_ID, Org, OrgApi, UserApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';

type OrgSortField = 'group_id' | 'display_name' | 'status' | 'updated_at';

export function OrgsPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [statusFilter, setStatusFilter] = useState('');
  const [sortBy, setSortBy] = useState<OrgSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [editing, setEditing] = useState<Org | null | undefined>(undefined); // undefined=关闭, null=新建
  const [managing, setManaging] = useState<Org | null>(null);
  const [delTarget, setDelTarget] = useState<Org | null>(null);

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

  const items = data?.items ?? [];

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
            <button className="btn primary sm" onClick={() => setEditing(null)}>
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
                          <span className="th-filter__label">{t('iam.status')}</span>
                          <TableColumnSort
                            iconOnly
                            label={t('iam.status')}
                            value={sortBy === 'status' ? sortOrder : ''}
                            options={sortOptions}
                            onChange={(value) => handleSortChange('status', value)}
                          />
                          <TableColumnFilter
                            iconOnly
                            label={t('iam.status')}
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
                        <td colSpan={5}>
                          <Empty text={t('common.empty')} />
                        </td>
                      </tr>
                    ) : (
                      items.map((o) => (
                        <tr key={o.group_id}>
                          <td
                            className="mono text-[11px] text-muted w-[25rem] max-w-[25rem] break-all"
                            title={o.group_id}
                          >
                            {o.group_id}
                          </td>
                          <td className="text-text-strong font-medium break-words">{o.display_name}</td>
                          <td className="whitespace-nowrap">
                            {o.status === 'active' ? t('common.enabled') : t('common.disabled')}
                          </td>
                          <td className="mono text-[11px] text-muted whitespace-nowrap">
                            {formatTime(o.updated_at)}
                          </td>
                          <td className="whitespace-nowrap min-w-[9.5rem]">
                            <div className="flex items-center gap-1">
                              <button className="btn sm" onClick={() => setManaging(o)}>
                                {t('iam.members')}
                              </button>
                              <button className="btn sm ghost" onClick={() => setEditing(o)}>
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

      {editing !== undefined && (
        <OrgModal
          org={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined);
            void reload();
          }}
        />
      )}
      {managing && <MembersModal org={managing} onClose={() => setManaging(null)} />}

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

function MembersModal({ org, onClose }: { org: Org; onClose: () => void }) {
  const { t } = useTranslation();
  const readOnly = org.group_id === NO_ORG_GROUP_ID; // 无组织=自动归类,只读
  const { data: membersData, loading, reload } = useAsync(() => OrgApi.listMembers(org.group_id), [org.group_id]);
  const { data: allUsersData } = useAsync(() => UserApi.list(), []);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState('');

  const members: IamUser[] = membersData?.users ?? [];
  const memberIds = useMemo(() => new Set(members.map((u) => u.user_id)), [members]);
  const candidates = useMemo(() => {
    const all = allUsersData?.items ?? [];
    const q = search.trim().toLowerCase();
    return all.filter((u) =>
      !memberIds.has(u.user_id) &&
      (!q || u.user_id.toLowerCase().includes(q) || (u.display_name ?? '').toLowerCase().includes(q)),
    );
  }, [allUsersData, memberIds, search]);

  async function add(uid: string) {
    setBusy(uid);
    try {
      await OrgApi.addMembers(org.group_id, [uid]);
      toast('success', t('success.saved'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy('');
    }
  }

  async function remove(uid: string) {
    setBusy(uid);
    try {
      await OrgApi.removeMember(org.group_id, uid);
      toast('success', t('success.deleted'));
      reload();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy('');
    }
  }

  return (
    <Modal
      open
      title={`${t('iam.members')} · ${org.display_name}`}
      onClose={onClose}
      footer={<button className="btn primary" onClick={onClose}>{t('common.close')}</button>}
    >
      <label className="label">{t('iam.currentMembers')} ({members.length})</label>
      <div style={{ maxHeight: 200, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {members.map((u) => (
          <div key={u.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 0' }}>
            <span>{u.display_name} <span className="text-xs text-muted mono">{u.user_id}</span></span>
            {!readOnly && (
              <button className="btn sm danger" disabled={busy === u.user_id} onClick={() => remove(u.user_id)}>{t('iam.removeMember')}</button>
            )}
          </div>
        ))}
        {!loading && members.length === 0 && <div className="text-xs text-muted">{t('iam.noMembers')}</div>}
      </div>

      {readOnly ? (
        <div className="text-xs text-muted" style={{ marginTop: 12 }}>{t('iam.noOrgReadonly')}</div>
      ) : (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.addMember')}</label>
          <input className="input" placeholder={t('iam.searchUser')} value={search} onChange={(e) => setSearch(e.target.value)} />
          <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8, marginTop: 6 }}>
            {candidates.map((u) => (
              <div key={u.user_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '3px 0' }}>
                <span>{u.display_name} <span className="text-xs text-muted mono">{u.user_id}</span></span>
                <button className="btn sm" disabled={busy === u.user_id} onClick={() => add(u.user_id)}>{t('iam.add')}</button>
              </div>
            ))}
            {candidates.length === 0 && <div className="text-xs text-muted">{t('iam.noCandidates')}</div>}
          </div>
        </>
      )}
    </Modal>
  );
}

function OrgModal({ org, onClose, onSaved }: { org: Org | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(true);
  const [displayName, setDisplayName] = useState(org?.display_name ?? '');
  const [status, setStatus] = useState(org?.status ?? 'active');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const next = { displayName: org?.display_name ?? '', status: org?.status ?? 'active' };
    setDisplayName(next.displayName);
    setStatus(next.status);
    markClean(next);
  }, [org, markClean]);

  async function save() {
    setBusy(true);
    try {
      if (org) await OrgApi.update(org.group_id, { display_name: displayName, status });
      else await OrgApi.create({ display_name: displayName });
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const draft = { displayName, status };
  const canSave = !!displayName.trim();

  return (
    <Modal
      open
      title={org ? t('iam.editOrg') : t('iam.newOrg')}
      onClose={onClose}
      dirty={isDirty(draft)}
      footer={
        <>
          <ModalCancelButton className="btn" />
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || !canSave} onClick={save}>{t('common.save')}</button>
        </>
      }
    >
      <label className="label">{t('iam.displayName')}</label>
      <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      {org && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.status')}</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
          <div className="text-xs text-muted" style={{ marginTop: 8 }}>{t('iam.groupId')}: <span className="mono">{org.group_id}</span></div>
        </>
      )}
    </Modal>
  );
}
