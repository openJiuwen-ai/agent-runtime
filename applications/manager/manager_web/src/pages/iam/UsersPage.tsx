import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as XLSX from 'xlsx';
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
import { isValidIdentityId, sanitizeIdentityIdInput } from '../../utils/identityId';

type UserSortField = 'user_id' | 'display_name' | 'is_admin' | 'status' | 'updated_at';

export function UsersPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [statusFilter, setStatusFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState(''); // '' | 'true' | 'false'
  const [sortBy, setSortBy] = useState<UserSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [editing, setEditing] = useState<IamUser | null | undefined>(undefined);
  const [showBatch, setShowBatch] = useState(false);
  const [delTarget, setDelTarget] = useState<IamUser | null>(null);

  const { data: orgsData } = useAsync(() => OrgApi.list(), []);
  const orgs = orgsData?.items ?? [];

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: UserSortField, value: ColumnSortValue) => {
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
      UserApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
        status: statusFilter || undefined,
        is_admin: roleFilter === '' ? undefined : roleFilter === 'true',
        sort_by: sortBy || undefined,
        sort_order: sortBy ? sortOrder : undefined,
      }),
    [page, pageSize, searchQuery, statusFilter, roleFilter, sortBy, sortOrder],
  );

  const items = data?.items ?? [];

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('iam.users')}>
              {t('iam.users')}
            </div>
            <div className="page-subtitle truncate" title={t('iam.usersSubtitle')}>
              {t('iam.usersSubtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('iam.usersSearchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
            <button className="btn sm" onClick={() => setShowBatch(true)}>
              {t('iam.batchNewUser')}
            </button>
            <button className="btn primary sm" onClick={() => setEditing(null)}>
              + {t('iam.newUser')}
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
                        <div className="th-filter">
                          <span className="th-filter__label">{t('iam.role')}</span>
                          <TableColumnSort
                            iconOnly
                            label={t('iam.role')}
                            value={sortBy === 'is_admin' ? sortOrder : ''}
                            options={sortOptions}
                            onChange={(value) => handleSortChange('is_admin', value)}
                          />
                          <TableColumnFilter
                            iconOnly
                            label={t('iam.role')}
                            value={roleFilter}
                            options={[
                              { value: '', label: t('common.all') },
                              { value: 'true', label: t('iam.roleAdmin') },
                              { value: 'false', label: t('iam.roleUser') },
                            ]}
                            onChange={(value) => {
                              setRoleFilter(value);
                              setPage(1);
                            }}
                          />
                        </div>
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
                        <td colSpan={6}>
                          <Empty text={t('common.empty')} />
                        </td>
                      </tr>
                    ) : (
                      items.map((u) => (
                        <tr key={u.user_id}>
                          <td
                            className="mono text-[11px] text-muted w-[25rem] max-w-[25rem] break-all"
                            title={u.user_id}
                          >
                            {u.user_id}
                          </td>
                          <td className="text-text-strong font-medium break-words">{u.display_name}</td>
                          <td className="whitespace-nowrap">
                            {u.is_admin ? (
                              <span className="badge">{t('iam.roleAdmin')}</span>
                            ) : (
                              t('iam.roleUser')
                            )}
                          </td>
                          <td className="whitespace-nowrap">
                            {u.status === 'active' ? t('common.enabled') : t('common.disabled')}
                          </td>
                          <td className="mono text-[11px] text-muted whitespace-nowrap">
                            {formatTime(u.updated_at)}
                          </td>
                          <td className="whitespace-nowrap min-w-[9.5rem]">
                            <div className="flex items-center gap-1">
                              <button className="btn sm ghost" onClick={() => setEditing(u)}>
                                {t('common.edit')}
                              </button>
                              <button className="btn sm danger" onClick={() => setDelTarget(u)}>
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
        <UserModal
          user={editing}
          orgs={orgs}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined);
            void reload();
          }}
        />
      )}
      {showBatch && (
        <BatchImportModal
          onClose={() => setShowBatch(false)}
          onDone={() => {
            void reload();
          }}
        />
      )}

      <ConfirmDialog
        open={!!delTarget}
        message={t('iam.confirmDeleteUser', {
          name: delTarget?.display_name ?? '',
          id: delTarget?.user_id ?? '',
        })}
        danger
        onConfirm={async () => {
          if (!delTarget) return;
          try {
            await UserApi.remove(delTarget.user_id);
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

type BatchRow = { username: string; password: string; display_name?: string; is_admin?: boolean; orgs?: string[] };
type BatchResp = {
  summary: { total: number; ok: number; failed: number };
  results: Array<{ row: number; username: string; ok: boolean; user_id?: string; warnings?: string[]; error?: string }>;
};

function parseBool(v: unknown): boolean {
  const s = String(v ?? '').trim().toLowerCase();
  return s === 'true' || s === '1' || s === 'yes' || s === 'y' || s === '是';
}

function BatchImportModal({
  onClose, onDone,
}: { onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [fileName, setFileName] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BatchResp | null>(null);

  function downloadTemplate() {
    const ws = XLSX.utils.aoa_to_sheet([
      ['username', 'password', 'display_name', 'is_admin', 'orgs'],
      ['zhangsan', 'Pass@123', '张三', 'false', '销售部,市场部'],
    ]);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'users');
    XLSX.writeFile(wb, 'users_template.xlsx');
  }

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFileName(f.name);
    setResult(null);
    const isCsv = /\.csv$/i.test(f.name);
    const reader = new FileReader();
    reader.onload = () => {
      try {
        // CSV 按 UTF-8 文本读（避免无 BOM 时 SheetJS 用非 UTF-8 码页导致中文乱码）；xlsx 仍按二进制读
        const wb = isCsv
          ? XLSX.read(reader.result as string, { type: 'string' })
          : XLSX.read(reader.result, { type: 'array' });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const json = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: '' });
        setRows(json.map((r) => ({
          username: String(r.username ?? '').trim(),
          password: String(r.password ?? '').trim(),
          display_name: String(r.display_name ?? '').trim() || undefined,
          is_admin: parseBool(r.is_admin),
          orgs: String(r.orgs ?? '').split(/[,，]/).map((s) => s.trim()).filter(Boolean),
        })));
      } catch (err) {
        toast('danger', String(err));
      }
    };
    if (isCsv) reader.readAsText(f, 'UTF-8');
    else reader.readAsArrayBuffer(f);
  }

  const invalidCount = rows.filter(
    (r) => !r.username || !r.password || !isValidIdentityId(r.username),
  ).length;

  async function submit() {
    setBusy(true);
    try {
      const res = await UserApi.batchCreate(rows);
      setResult(res);
      if (res.summary.ok > 0) onDone();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={t('iam.batchNewUser')}
      onClose={onClose}
      dirty={rows.length > 0 && !result}
      footer={
        <>
          <ModalCancelButton className="btn">{t('common.close')}</ModalCancelButton>
          <button
            className="btn primary"
            style={{ marginLeft: 8 }}
            disabled={busy || rows.length === 0 || invalidCount > 0 || !!result}
            onClick={submit}
          >
            {t('iam.batchImport')}{rows.length ? ` (${rows.length})` : ''}
          </button>
        </>
      }
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
        <button className="btn sm" onClick={downloadTemplate}>{t('iam.batchDownloadTemplate')}</button>
        <input type="file" accept=".xlsx,.csv" onChange={onFile} />
      </div>
      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>{t('iam.batchHint')}</div>
      {fileName && <div className="text-xs" style={{ marginBottom: 6 }}>{fileName}</div>}

      {rows.length > 0 && !result && (
        <>
          <div className="text-xs" style={{ marginBottom: 4 }}>
            {t('iam.batchPreview', { n: rows.length })}
            {invalidCount > 0 && <span style={{ color: '#c0392b' }}> · {t('iam.batchInvalid', { n: invalidCount })}</span>}
          </div>
          <div style={{ maxHeight: 220, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6 }}>
            <table className="table" style={{ width: '100%', fontSize: 12 }}>
              <thead><tr><th>{t('iam.username')}</th><th>{t('iam.displayName')}</th><th>{t('iam.admin')}</th><th>{t('iam.belongOrgs')}</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={!r.username || !r.password || !isValidIdentityId(r.username) ? { background: 'rgba(192,57,43,0.08)' } : undefined}>
                    <td>{r.username || '—'}{!r.password && <span style={{ color: '#c0392b' }}> ·{t('iam.batchNoPwd')}</span>}{!!r.username && !isValidIdentityId(r.username) && <span style={{ color: '#c0392b' }}> ·{t('iam.idCharsetInvalid', { field: t('iam.username') })}</span>}</td>
                    <td>{r.display_name || r.username}</td>
                    <td>{r.is_admin ? '✓' : ''}</td>
                    <td className="mono text-xs">{(r.orgs || []).join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {result && (
        <div>
          <div style={{ marginBottom: 8 }}>
            {t('iam.batchSummary', { total: result.summary.total, ok: result.summary.ok, failed: result.summary.failed })}
          </div>
          <div style={{ maxHeight: 240, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6, padding: 8, fontSize: 12 }}>
            {result.results.map((r) => (
              <div key={r.row} style={{ padding: '2px 0' }}>
                {r.ok ? '✅' : '❌'} #{r.row} {r.username}
                {r.error && <span style={{ color: '#c0392b' }}> — {r.error}</span>}
                {r.warnings && r.warnings.length > 0 && <span style={{ color: '#b8860b' }}> — {r.warnings.join('；')}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}

function UserModal({ user, orgs, onClose, onSaved }: { user: IamUser | null; orgs: Org[]; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const isEdit = !!user;
  const { markClean, isDirty } = useFormDirty(true);
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isAdmin, setIsAdmin] = useState(user?.is_admin ?? false);
  const [status, setStatus] = useState(user?.status ?? 'active');
  const [selectedOrgs, setSelectedOrgs] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  // 不展示"无组织":未勾选任何组织即自动归为无组织
  const realOrgs = orgs.filter((o) => o.group_id !== NO_ORG_GROUP_ID);

  useEffect(() => {
    const base = {
      displayName: user?.display_name ?? '',
      username: '',
      password: '',
      isAdmin: user?.is_admin ?? false,
      status: user?.status ?? 'active',
      orgs: [] as string[],
    };
    markClean(base);
    if (!user) return;
    UserApi.get(user.user_id)
      .then((d) => {
        const orgIds = [...(d.group_ids ?? [])].sort();
        setSelectedOrgs(new Set(orgIds));
        markClean({ ...base, orgs: orgIds });
      })
      .catch(() => undefined);
  }, [user, markClean]);

  function toggleOrg(gid: string) {
    setSelectedOrgs((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid); else next.add(gid);
      return next;
    });
  }

  async function save() {
    if (!isEdit) {
      if (!isValidIdentityId(username)) {
        toast('warn', t('iam.idCharsetInvalid', { field: t('iam.username') }));
        return;
      }
    }
    setBusy(true);
    try {
      let uid = user?.user_id;
      if (isEdit && user) {
        await UserApi.update(user.user_id, {
          display_name: displayName, is_admin: isAdmin, status,
          ...(password ? { password } : {}),
        });
      } else {
        const created = await UserApi.create({ display_name: displayName, username: username.trim(), password, is_admin: isAdmin });
        uid = created.user_id;
      }
      if (uid) await UserApi.setOrgs(uid, Array.from(selectedOrgs));
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const canSave = displayName.trim() && (isEdit || (isValidIdentityId(username) && password));
  const draft = {
    displayName,
    username,
    password,
    isAdmin,
    status,
    orgs: [...selectedOrgs].sort(),
  };

  return (
    <Modal
      open
      title={isEdit ? t('iam.editUser') : t('iam.newUser')}
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

      {!isEdit && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.username')}</label>
          <input
            className="input mono"
            value={username}
            maxLength={64}
            onChange={(e) => setUsername(sanitizeIdentityIdInput(e.target.value))}
          />
          <div className="text-xs text-muted" style={{ marginTop: 4 }}>{t('iam.usernameHint')}</div>
        </>
      )}

      <label className="label" style={{ marginTop: 12 }}>{isEdit ? t('iam.resetPassword') : t('iam.password')}</label>
      <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />

      <label className="label" style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} /> {t('iam.admin')}
      </label>

      {isEdit && (
        <>
          <label className="label" style={{ marginTop: 12 }}>{t('iam.status')}</label>
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">active</option>
            <option value="disabled">disabled</option>
          </select>
        </>
      )}

      <label className="label" style={{ marginTop: 12 }}>{t('iam.belongOrgs')}</label>
      <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{t('iam.noOrgHint')}</div>
      <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {realOrgs.map((o) => (
          <label key={o.group_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' }}>
            <input type="checkbox" checked={selectedOrgs.has(o.group_id)} onChange={() => toggleOrg(o.group_id)} />
            {o.display_name} <span className="text-xs text-muted mono">{o.group_id}</span>
          </label>
        ))}
        {realOrgs.length === 0 && <div className="text-xs text-muted">{t('iam.noOrgs')}</div>}
      </div>
    </Modal>
  );
}
