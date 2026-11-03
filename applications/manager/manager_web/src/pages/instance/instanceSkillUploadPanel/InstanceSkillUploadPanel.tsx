import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../../hooks/useAsync';
import {
  ApiError,
  IamUser,
  hasPermission,
  Org,
  OrgApi,
  SkillUploadApi,
  SkillUploadPermission,
  UserApi,
} from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import { Empty } from '../../../components/Empty';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { Switch } from '../../../components/Switch';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { formatTime } from '../../../utils/format';

type SubjectType = 'user' | 'org';
type DirectoryItem = { id: string; label: string };

function toDirItems_orgs(items: Org[] | undefined): DirectoryItem[] {
  return (items ?? []).map((it) => ({
    id: it.group_id,
    label: it.display_name && it.display_name !== it.group_id
      ? `${it.display_name} (${it.group_id})`
      : it.group_id,
  }));
}

function toDirItems_users(items: IamUser[] | undefined): DirectoryItem[] {
  return (items ?? []).map((it) => ({
    id: it.user_id,
    label: it.display_name && it.display_name !== it.user_id
      ? `${it.display_name} (${it.user_id})`
      : it.user_id,
  }));
}

interface Props {
  instanceId: string;
}

/**
 * Skill 上传授权面板：用户/组织两块表格 + 页内展开添加。
 *
 * 管理员只可选择 identity 目录中的用户/组织；后端按 cluster_id（实例边界）
 * 维护授权记录，直接授权不绕过企业实例的准入检查（后端 user_can_access_context
 * 校验三元组真实归属）。
 */
export function InstanceSkillUploadPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canWrite = hasPermission(user, 'skill:upload:grant');

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="page-header">
        <div>
          <div className="page-title">{t('skillUpload.title')}</div>
          <div className="page-subtitle">{t('skillUpload.subtitle')}</div>
        </div>
      </div>
      <SkillGrantSection instanceId={instanceId} subjectType="user" canWrite={canWrite} />
      <SkillGrantSection instanceId={instanceId} subjectType="org" canWrite={canWrite} />
    </div>
  );
}

import { useAuth } from '../../../auth/AuthContext';

interface SectionProps {
  instanceId: string;
  subjectType: SubjectType;
  canWrite: boolean;
}

function SkillGrantSection({ instanceId, subjectType, canWrite }: SectionProps) {
  const { t } = useTranslation();
  const isUser = subjectType === 'user';

  const { data, loading, error, reload } = useAsync(
    () => SkillUploadApi.list({
      cluster_id: instanceId,
      subject_type: subjectType,
      page: 1,
      page_size: 200,
    }),
    [instanceId, subjectType],
  );

  const items = data?.items ?? [];

  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 border-b border-border-default px-4 py-3">
        <div className="text-sm font-medium">
          {isUser ? t('skillUpload.userSection') : t('skillUpload.orgSection')}
        </div>
        {canWrite && <AddGrantInline instanceId={instanceId} subjectType={subjectType} onSaved={reload} />}
      </div>

      <div className="overflow-x-auto">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : (
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th>{isUser ? t('skillUpload.subjectTypes.user') : t('skillUpload.subjectTypes.org')}</th>
                {isUser && <th>{t('skillUpload.source')}</th>}
                <th>{t('skillUpload.sourceOrderNum')}</th>
                <th>{t('skillUpload.grantedBy')}</th>
                <th>{t('skillUpload.expiresAt')}</th>
                <th>{t('skillUpload.enabled')}</th>
                <th>{t('skillUpload.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={isUser ? 7 : 6}><Empty text={t('skillUpload.empty')} /></td></tr>
              ) : items.map((row) => (
                <GrantRow
                  key={row.id}
                  row={row}
                  isUser={isUser}
                  canWrite={canWrite}
                  onSaved={reload}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function GrantRow({
  row,
  isUser,
  canWrite,
  onSaved,
}: {
  row: SkillUploadPermission;
  isUser: boolean;
  canWrite: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [revokeOpen, setRevokeOpen] = useState(false);

  const toggleEnabled = async (next: boolean) => {
    try {
      await SkillUploadApi.update(row.id, { enabled: next });
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    }
  };

  const revoke = async () => {
    try {
      await SkillUploadApi.revoke({
        cluster_id: row.cluster_id,
        subject_type: row.subject_type,
        ids: [row.subject_id],
      });
      toast('success', t('skillUpload.revoked'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    }
  };

  return (
    <>
      <tr>
        <td className="mono text-[12px]">{row.subject_id}</td>
        {isUser && (
          <td>
            <span className="badge">{t(`skillUpload.sources.${row.source}`)}</span>
          </td>
        )}
        <td className="mono text-[11px] text-muted break-all">{row.source_order_num || '—'}</td>
        <td className="text-[12px]">{row.granted_by || '—'}</td>
        <td className="text-[12px]">
          {row.expires_at ? formatTime(row.expires_at) : t('skillUpload.neverExpires')}
        </td>
        <td>
          <Switch
            checked={row.enabled}
            disabled={!canWrite}
            onChange={(next) => void toggleEnabled(next)}
            aria-label={t('skillUpload.enabled')}
          />
        </td>
        <td>
          {canWrite && (
            <button
              className="btn danger sm"
              onClick={() => setRevokeOpen(true)}
            >
              {t('skillUpload.revoke')}
            </button>
          )}
        </td>
      </tr>
      <ConfirmDialog
        open={revokeOpen}
        title={t('skillUpload.revoke')}
        message={t('skillUpload.revokeConfirm')}
        danger
        onConfirm={() => void revoke()}
        onClose={() => setRevokeOpen(false)}
      />
    </>
  );
}

function AddGrantInline({
  instanceId,
  subjectType,
  onSaved,
}: {
  instanceId: string;
  subjectType: SubjectType;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const isUser = subjectType === 'user';
  const [open, setOpen] = useState(false);
  const [dirSearch, setDirSearch] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const { data: orgsData } = useAsync(
    () => OrgApi.list({ page: 1, page_size: 200, search: dirSearch || undefined, status: 'active' }),
    [subjectType, open, dirSearch],
  );
  const { data: usersData } = useAsync(
    () => UserApi.list({ page: 1, page_size: 200, search: dirSearch || undefined, status: 'active' }),
    [subjectType, open, dirSearch],
  );
  const directoryItems = useMemo<DirectoryItem[]>(
    () => isUser
      ? toDirItems_users(usersData?.items)
      : toDirItems_orgs(orgsData?.items),
    [isUser, orgsData, usersData],
  );

  const reset = () => {
    setSelectedIds([]);
    setDirSearch('');
  };

  const submit = async () => {
    if (!selectedIds.length) return;
    setSaving(true);
    try {
      await SkillUploadApi.grant({
        cluster_id: instanceId,
        subject_type: subjectType,
        ids: selectedIds,
        expires_at: null,
        enabled: true,
      });
      toast('success', t('skillUpload.saved'));
      setOpen(false);
      reset();
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', {
        detail: e instanceof ApiError ? e.detail : (e as Error).message,
      }));
    } finally {
      setSaving(false);
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  if (!open) {
    return (
      <button
        className="btn primary sm"
        onClick={() => setOpen(true)}
      >
        {isUser ? t('skillUpload.addUser') : t('skillUpload.addOrg')}
      </button>
    );
  }

  return (
    <div className="w-full min-w-0">
      <div className="rounded border border-border-default bg-bg-card p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-sm font-medium">
            {isUser ? t('skillUpload.addUser') : t('skillUpload.addOrg')}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="btn ghost sm"
              onClick={() => { setOpen(false); reset(); }}
            >
              {t('common.cancel')}
            </button>
            <button
              className="btn primary sm"
              disabled={saving || selectedIds.length === 0}
              onClick={() => void submit()}
            >
              {t('skillUpload.save')}
            </button>
          </div>
        </div>
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
    </div>
  );
}
