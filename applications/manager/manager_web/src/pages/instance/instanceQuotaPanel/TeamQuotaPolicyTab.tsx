import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../../auth/AuthContext';
import { ApiError, hasPermission, TeamQuotaApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useFormDirty } from '../../../hooks/useFormDirty';
import type { TeamQuotaPolicy, WorkspaceQuotaMatchExpr } from '../../../types';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { Switch } from '../../../components/Switch';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { HintTooltip } from '../../../components/HintTooltip';
import { toast } from '../../../stores/uiStore';
import { formatTime } from '../../../utils/format';
import { matchExprToEditorString } from '../../../utils/matchExpr';

const FIELD_MAX_LENGTH = {
  policy_name: 128,
  policy_desc: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function isFullMatch(expr: WorkspaceQuotaMatchExpr | undefined): boolean {
  if (expr == null) return true;
  if (Array.isArray(expr)) return expr.length === 0;
  const text = String(expr).trim();
  if (!text || text === '[]') return true;
  return !text.includes('==') && !text.includes('!=') && !text.includes(' in ');
}

function editorToMatchExpr(value: string): WorkspaceQuotaMatchExpr {
  const text = value.trim();
  if (!text) return [];
  if (text.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item));
    } catch {
      /* keep as string */
    }
  }
  return text;
}

interface FormState {
  policyName: string;
  policyDesc: string;
  matchExpr: string;
  priority: number;
  maxTeams: number;
  maxTeammates: number;
  maxTeamsPerOrg: number;
  enabled: boolean;
}

const emptyForm: FormState = {
  policyName: 'team-quota',
  policyDesc: '',
  matchExpr: '',
  priority: 0,
  maxTeams: 5,
  maxTeammates: 5,
  maxTeamsPerOrg: 0,
  enabled: true,
};

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && (
        <span className="text-danger ml-0.5" aria-hidden="true">
          *
        </span>
      )}
    </label>
  );
}

export function TeamQuotaPolicyTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canWrite = hasPermission(user, 'quota:write');
  const { data, loading, error, reload } = useAsync(() => TeamQuotaApi.list(instanceId), [instanceId]);
  const rows = data?.items ?? [];
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TeamQuotaPolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [delTarget, setDelTarget] = useState<TeamQuotaPolicy | null>(null);
  const { markClean, isDirty } = useFormDirty(modalOpen);

  useEffect(() => {
    if (!modalOpen) return;
    const next: FormState = editing
      ? {
          policyName: clipField(editing.policy_name ?? '', FIELD_MAX_LENGTH.policy_name),
          policyDesc: clipField(editing.policy_desc ?? '', FIELD_MAX_LENGTH.policy_desc),
          matchExpr: matchExprToEditorString(
            Array.isArray(editing.match_expr) || typeof editing.match_expr === 'string'
              ? editing.match_expr
              : '',
          ),
          priority: editing.priority,
          maxTeams: editing.max_teams_per_user,
          maxTeammates: editing.max_teammates_per_team,
          maxTeamsPerOrg: editing.max_teams_per_org ?? 0,
          enabled: editing.enabled,
        }
      : emptyForm;
    setForm(next);
    markClean(next);
  }, [modalOpen, editing, markClean]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const save = async () => {
    const name = form.policyName.trim();
    if (!name) {
      toast('warn', t('instanceDetail.teamQuota.fieldRequired', {
        field: t('instanceDetail.teamQuota.policyName'),
      }));
      return;
    }
    if (!Number.isInteger(form.priority)) {
      toast('warn', t('instanceDetail.teamQuota.invalidPriority'));
      return;
    }
    if (
      !Number.isInteger(form.maxTeams) || form.maxTeams < 1 || form.maxTeams > 1000
      || !Number.isInteger(form.maxTeammates) || form.maxTeammates < 1 || form.maxTeammates > 1000
    ) {
      toast('warn', t('instanceDetail.teamQuota.invalidLimit'));
      return;
    }
    if (!Number.isInteger(form.maxTeamsPerOrg) || form.maxTeamsPerOrg < 0 || form.maxTeamsPerOrg > 1000) {
      toast('warn', t('instanceDetail.teamQuota.invalidOrgLimit'));
      return;
    }
    setSaving(true);
    try {
      await TeamQuotaApi.upsert(instanceId, {
        policy_id: editing?.policy_id,
        policy_name: name,
        policy_desc: form.policyDesc.trim() || null,
        match_expr: editorToMatchExpr(form.matchExpr),
        priority: form.priority,
        max_teams_per_user: form.maxTeams,
        max_teammates_per_team: form.maxTeammates,
        max_teams_per_org: form.maxTeamsPerOrg,
        enabled: form.enabled,
      });
      toast('success', t('common.save'));
      setModalOpen(false);
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (row: TeamQuotaPolicy) => {
    try {
      await TeamQuotaApi.remove(instanceId, row.policy_id);
      toast('success', t('success.deleted'));
      setDelTarget(null);
      await reload();
    } catch (e) {
      toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="min-w-0">
          <div className="page-header mb-0 w-full min-w-0 flex-wrap gap-y-3">
            <div className="page-title min-w-[7.5rem] shrink-0 truncate">
              {t('instanceDetail.teamQuota.tabs.policy')}
            </div>
            <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
              <button className="btn sm" onClick={() => void reload()}>
                {t('common.refresh')}
              </button>
              {canWrite && (
                <button
                  className="btn primary sm"
                  onClick={() => {
                    setEditing(null);
                    setModalOpen(true);
                  }}
                >
                  + {t('instanceDetail.teamQuota.new')}
                </button>
              )}
            </div>
          </div>
          <div className="page-subtitle truncate pl-3">{t('instanceDetail.teamQuota.subtitle')}</div>
          <div className="mt-2 pl-3 text-sm text-muted">
            <div className="font-medium text-[var(--text-strong)]">
              {t('instanceDetail.teamQuota.effectiveTitle')}
            </div>
            <div className="mt-0.5 whitespace-normal">{t('instanceDetail.teamQuota.effectiveRule')}</div>
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
                    <th>{t('instanceDetail.teamQuota.policyName')}</th>
                    <th>{t('instanceDetail.teamQuota.matchExpr')}</th>
                    <th>{t('instanceDetail.teamQuota.priority')}</th>
                    <th>{t('instanceDetail.teamQuota.maxTeams')}</th>
                    <th>{t('instanceDetail.teamQuota.maxTeamsPerOrg')}</th>
                    <th>{t('instanceDetail.teamQuota.maxTeammates')}</th>
                    <th>{t('common.enabled')}</th>
                    <th>{t('instanceDetail.teamQuota.updatedAt')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={9}>
                        <Empty text={t('common.empty')} />
                      </td>
                    </tr>
                  ) : (
                    rows.map((row) => (
                      <tr key={row.policy_id}>
                        <td>
                          <div className="font-medium">{row.policy_name}</div>
                          {row.policy_desc ? (
                            <div className="text-xs text-muted">{row.policy_desc}</div>
                          ) : null}
                        </td>
                        <td>
                          {isFullMatch(row.match_expr)
                            ? t('instanceDetail.teamQuota.fullMatch')
                            : matchExprToEditorString(
                                Array.isArray(row.match_expr) || typeof row.match_expr === 'string'
                                  ? row.match_expr
                                  : '',
                              )}
                        </td>
                        <td>{row.priority}</td>
                        <td>{row.max_teams_per_user}</td>
                        <td>{row.max_teams_per_org ?? 0}</td>
                        <td>{row.max_teammates_per_team}</td>
                        <td>
                          <Switch
                            checked={row.enabled}
                            disabled={!canWrite}
                            aria-label={t('common.enabled')}
                            onChange={(checked) => {
                              void TeamQuotaApi.upsert(instanceId, {
                                policy_id: row.policy_id,
                                policy_name: row.policy_name,
                                policy_desc: row.policy_desc,
                                match_expr: row.match_expr,
                                priority: row.priority,
                                max_teams_per_user: row.max_teams_per_user,
                                max_teammates_per_team: row.max_teammates_per_team,
                                max_teams_per_org: row.max_teams_per_org ?? 0,
                                enabled: checked,
                              }).then(() => reload()).catch((e) => {
                                toast('danger', t('errors.saveFailed', {
                                  detail: e instanceof ApiError ? e.detail : (e as Error).message,
                                }));
                              });
                            }}
                          />
                        </td>
                        <td>{formatTime(row.updated_at)}</td>
                        <td className="whitespace-nowrap">
                          {canWrite && (
                            <>
                              <button
                                className="btn sm ghost"
                                onClick={() => {
                                  setEditing(row);
                                  setModalOpen(true);
                                }}
                              >
                                {t('common.edit')}
                              </button>
                              <button className="btn sm ghost text-danger" onClick={() => setDelTarget(row)}>
                                {t('common.delete')}
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={modalOpen}
        title={editing ? t('instanceDetail.teamQuota.edit') : t('instanceDetail.teamQuota.new')}
        onClose={() => setModalOpen(false)}
        dirty={isDirty(form)}
        size="lg"
        footer={
          <>
            <ModalCancelButton />
            <button className="btn primary" onClick={() => void save()} disabled={saving}>
              {saving ? t('common.loading') : t('common.save')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-3">
          <div className="grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('instanceDetail.teamQuota.policyName')}</FieldLabel>
              <LimitedTextInput
                value={form.policyName}
                maxLength={FIELD_MAX_LENGTH.policy_name}
                onChange={(value) => update('policyName', value)}
              />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.teamQuota.priority')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.teamQuota.priorityHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                step={1}
                value={Number.isInteger(form.priority) ? form.priority : ''}
                onChange={(e) => update('priority', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
          </div>
          <div>
            <FieldLabel>{t('instanceDetail.teamQuota.policyDesc')}</FieldLabel>
            <LimitedTextInput
              value={form.policyDesc}
              maxLength={FIELD_MAX_LENGTH.policy_desc}
              onChange={(value) => update('policyDesc', value)}
            />
          </div>
          <div>
            <FieldLabel required>{t('instanceDetail.teamQuota.matchExpr')}</FieldLabel>
            <MatchExprEditor value={form.matchExpr} onChange={(value) => update('matchExpr', value)} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.teamQuota.maxTeams')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.teamQuota.maxTeamsHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                min={1}
                max={1000}
                step={1}
                value={Number.isInteger(form.maxTeams) ? form.maxTeams : ''}
                onChange={(e) => update('maxTeams', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
            <div>
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.teamQuota.maxTeammates')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.teamQuota.maxTeammatesHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                min={1}
                max={1000}
                step={1}
                value={Number.isInteger(form.maxTeammates) ? form.maxTeammates : ''}
                onChange={(e) => update('maxTeammates', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
            <div>
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.teamQuota.maxTeamsPerOrg')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.teamQuota.maxTeamsPerOrgHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                min={0}
                max={1000}
                step={1}
                value={Number.isInteger(form.maxTeamsPerOrg) ? form.maxTeamsPerOrg : ''}
                onChange={(e) => update('maxTeamsPerOrg', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={delTarget != null}
        title={t('instanceDetail.teamQuota.deleteTitle')}
        message={t('instanceDetail.teamQuota.deleteMessage')}
        danger
        onClose={() => setDelTarget(null)}
        onConfirm={() => {
          if (delTarget) void remove(delTarget);
        }}
      />
    </>
  );
}
