import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { Modal } from '../../components/Modal';
import { Pagination } from '../../components/Pagination';
import { Switch } from '../../components/Switch';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useAuth } from '../../auth/AuthContext';
import {
  ApiError,
  hasPermission,
  InstanceApi,
  IamUser,
  Org,
  OrgApi,
  SkillUploadApi,
  SkillUploadPermission,
  UserApi,
} from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';

type SubjectType = 'user' | 'org';

type DirectoryItem = { id: string; label: string };

function toDirectoryItems_orgs(items: Org[] | undefined): DirectoryItem[] {
  return (items ?? []).map((it) => ({
    id: it.group_id,
    label: it.display_name && it.display_name !== it.group_id
      ? `${it.display_name} (${it.group_id})`
      : it.group_id,
  }));
}

function toDirectoryItems_users(items: IamUser[] | undefined): DirectoryItem[] {
  return (items ?? []).map((it) => ({
    id: it.user_id,
    label: it.display_name && it.display_name !== it.user_id
      ? `${it.display_name} (${it.user_id})`
      : it.user_id,
  }));
}

export function SkillUploadGrantsPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canWrite = hasPermission(user, 'skill:upload:grant');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [subjectTypeFilter, setSubjectTypeFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [enabledFilter, setEnabledFilter] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [subjectType, setSubjectType] = useState<SubjectType>('user');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [dirSearch, setDirSearch] = useState('');
  const [expiresInput, setExpiresInput] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<SkillUploadPermission | null>(null);

  const { data: instancesData } = useAsync(
    () => InstanceApi.list({ page: 1, page_size: 200 }),
    [],
  );
  const clusterOptions = useMemo(
    () => (instancesData?.items ?? []).map((it) => it.jiuwenclaw_id).filter(Boolean),
    [instancesData],
  );
  const [clusterId, setClusterId] = useState('');
  useEffect(() => {
    if (!clusterId && clusterOptions.length > 0) {
      setClusterId(clusterOptions[0]);
    }
  }, [clusterOptions, clusterId]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery, subjectTypeFilter, sourceFilter, enabledFilter, clusterId]);

  const { data, loading, error, reload } = useAsync(
    () => clusterId
      ? SkillUploadApi.list({
          cluster_id: clusterId,
          page,
          page_size: pageSize,
          search: searchQuery,
          subject_type: subjectTypeFilter === '' ? undefined : (subjectTypeFilter as SubjectType),
          source: sourceFilter === ''
            ? undefined
            : (sourceFilter as 'admin' | 'approval'),
          enabled: enabledFilter === ''
            ? undefined
            : enabledFilter === 'true',
        })
      : Promise.resolve({ items: [], total: 0, page: 1, page_size: pageSize }),
    [page, pageSize, searchQuery, subjectTypeFilter, sourceFilter, enabledFilter, clusterId],
  );

  // 目录拉取：随 subjectType 切换走 OrgApi / UserApi（identity 中心）。
  // useAsync 已记忆依赖，dirSearch 防抖由用户输入节奏决定；page_size=200 足够覆盖常规目录。
  const { data: orgsData } = useAsync(
    () => OrgApi.list({ page: 1, page_size: 200, search: dirSearch || undefined, status: 'active' }),
    [subjectType, addOpen, dirSearch],
  );
  const { data: usersData } = useAsync(
    () => UserApi.list({ page: 1, page_size: 200, search: dirSearch || undefined, status: 'active' }),
    [subjectType, addOpen, dirSearch],
  );
  const directoryItems = useMemo<DirectoryItem[]>(
    () => subjectType === 'org'
      ? toDirectoryItems_orgs(orgsData?.items)
      : toDirectoryItems_users(usersData?.items),
    [subjectType, orgsData, usersData],
  );

  const items = data?.items ?? [];
  const subjectTypeOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    { value: 'user', label: t('skillUpload.subjectTypes.user') },
    { value: 'org', label: t('skillUpload.subjectTypes.org') },
  ], [t]);
  const sourceOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    { value: 'admin', label: t('skillUpload.sources.admin') },
    { value: 'approval', label: t('skillUpload.sources.approval') },
  ], [t]);
  const enabledOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    { value: 'true', label: t('skillUpload.enabledTrue') },
    { value: 'false', label: t('skillUpload.enabledFalse') },
  ], [t]);

  const openAdd = () => {
    setSubjectType('user');
    setSelectedIds([]);
    setDirSearch('');
    setExpiresInput('');
    setEnabled(true);
    setAddOpen(true);
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const submitAdd = async () => {
    if (!selectedIds.length || !clusterId) return;
    setSaving(true);
    try {
      await SkillUploadApi.grant({
        cluster_id: clusterId,
        subject_type: subjectType,
        ids: selectedIds,
        expires_at: expiresInput ? new Date(expiresInput).toISOString() : null,
        enabled,
      });
      toast('success', t('skillUpload.saved'));
      setAddOpen(false);
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (row: SkillUploadPermission, next: boolean) => {
    try {
      await SkillUploadApi.update(row.id, { enabled: next });
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    }
  };

  const revoke = async () => {
    if (!revokeTarget) return;
    try {
      await SkillUploadApi.revoke({
        cluster_id: revokeTarget.cluster_id,
        subject_type: revokeTarget.subject_type,
        ids: [revokeTarget.subject_id],
      });
      toast('success', t('skillUpload.revoked'));
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[10rem]">
            <div className="page-title">{t('skillUpload.title')}</div>
            <div className="page-subtitle">{t('skillUpload.subtitle')}</div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <select
              className="input sm"
              value={clusterId}
              onChange={(e) => setClusterId(e.target.value)}
              aria-label={t('skillUpload.cluster')}
            >
              {clusterOptions.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('skillUpload.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
            {canWrite && (
              <button className="btn primary sm" onClick={openAdd} disabled={!clusterId}>
                {t('skillUpload.add')}
              </button>
            )}
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
                    <th>{t('skillUpload.subjectId')}</th>
                    <th>
                      <TableColumnFilter
                        label={t('skillUpload.subjectType')}
                        value={subjectTypeFilter}
                        options={subjectTypeOptions}
                        onChange={setSubjectTypeFilter}
                      />
                    </th>
                    <th>
                      <TableColumnFilter
                        label={t('skillUpload.source')}
                        value={sourceFilter}
                        options={sourceOptions}
                        onChange={setSourceFilter}
                      />
                    </th>
                    <th>{t('skillUpload.sourceOrderNum')}</th>
                    <th>{t('skillUpload.grantedBy')}</th>
                    <th>{t('skillUpload.expiresAt')}</th>
                    <th>
                      <TableColumnFilter
                        label={t('skillUpload.enabled')}
                        value={enabledFilter}
                        options={enabledOptions}
                        onChange={setEnabledFilter}
                      />
                    </th>
                    <th>{t('skillUpload.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 ? (
                    <tr><td colSpan={8}><Empty text={t('skillUpload.empty')} /></td></tr>
                  ) : items.map((row) => (
                    <tr key={row.id}>
                      <td className="mono text-[12px]">{row.subject_id}</td>
                      <td>
                        <span className="badge">
                          {t(`skillUpload.subjectTypes.${row.subject_type}`)}
                        </span>
                      </td>
                      <td>
                        <span className="badge">
                          {t(`skillUpload.sources.${row.source}`)}
                        </span>
                      </td>
                      <td className="mono text-[11px] text-muted break-all">
                        {row.source_order_num || '—'}
                      </td>
                      <td className="text-[12px]">{row.granted_by || '—'}</td>
                      <td className="text-[12px]">
                        {row.expires_at ? formatTime(row.expires_at) : t('skillUpload.neverExpires')}
                      </td>
                      <td>
                        <Switch
                          checked={row.enabled}
                          disabled={!canWrite}
                          onChange={(next) => void toggleEnabled(row, next)}
                          aria-label={t('skillUpload.enabled')}
                        />
                      </td>
                      <td>
                        {canWrite && (
                          <button
                            className="btn danger sm"
                            onClick={() => setRevokeTarget(row)}
                          >
                            {t('skillUpload.revoke')}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {data && data.total > 0 && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data.total}
            onChange={(nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            }}
          />
        )}
      </div>

      <Modal
        open={addOpen}
        title={t('skillUpload.addTitle')}
        onClose={() => setAddOpen(false)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setAddOpen(false)}>
              {t('common.cancel')}
            </button>
            <button
              className="btn primary"
              disabled={saving || selectedIds.length === 0}
              onClick={() => void submitAdd()}
            >
              {t('skillUpload.save')}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div>
            <div className="field-label">{t('skillUpload.cluster')}</div>
            <div className="mono text-[12px] text-muted">{clusterId}</div>
          </div>
          <div>
            <div className="field-label">{t('skillUpload.subjectTypeLabel')}</div>
            <select
              className="input"
              value={subjectType}
              onChange={(e) => {
                setSubjectType(e.target.value as SubjectType);
                setSelectedIds([]);
                setDirSearch('');
              }}
            >
              <option value="user">{t('skillUpload.subjectTypes.user')}</option>
              <option value="org">{t('skillUpload.subjectTypes.org')}</option>
            </select>
          </div>
          <div>
            <div className="field-label">{t('skillUpload.idsLabel')}</div>
            <ListSearchInput
              value={dirSearch}
              onChange={setDirSearch}
              placeholder={t('skillUpload.dirSearchPlaceholder')}
              className="mb-2"
            />
            <div className="max-h-56 overflow-y-auto rounded border border-border-default">
              {directoryItems.length === 0 ? (
                <div className="p-3 text-sm text-muted">{t('skillUpload.dirEmpty')}</div>
              ) : directoryItems.map((item) => {
                const checked = selectedIds.includes(item.id);
                return (
                  <label
                    key={item.id}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-bg-hover"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSelect(item.id)}
                    />
                    <span className="mono text-[12px]">{item.label}</span>
                  </label>
                );
              })}
            </div>
            {selectedIds.length > 0 && (
              <div className="mt-1 text-xs text-muted">
                {t('skillUpload.selectedCount', { count: selectedIds.length })}
              </div>
            )}
          </div>
          <div>
            <div className="field-label">{t('skillUpload.expiresLabel')}</div>
            <input
              className="input"
              type="datetime-local"
              value={expiresInput}
              onChange={(e) => setExpiresInput(e.target.value)}
            />
          </div>
          <div>
            <div className="field-label">{t('skillUpload.enabled')}</div>
            <Switch
              checked={enabled}
              onChange={setEnabled}
              aria-label={t('skillUpload.enabled')}
            />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={revokeTarget !== null}
        title={t('skillUpload.revoke')}
        message={t('skillUpload.revokeConfirm')}
        danger
        onConfirm={() => void revoke()}
        onClose={() => setRevokeTarget(null)}
      />
    </>
  );
}
