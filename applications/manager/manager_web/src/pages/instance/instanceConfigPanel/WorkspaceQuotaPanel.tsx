import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, WorkspaceQuotaApi } from '../../../services/api';
import { useAsync } from '../../../hooks/useAsync';
import { useFormDirty } from '../../../hooks/useFormDirty';
import { useListSearch } from '../../../hooks/useListSearch';
import type { WorkspaceQuotaMatchExpr, WorkspaceQuotaPolicy } from '../../../types';
import { Empty } from '../../../components/Empty';
import { ConfirmDialog } from '../../../components/ConfirmDialog';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { MatchExprEditor } from '../../../components/MatchExprEditor';
import { Pagination } from '../../../components/Pagination';
import { Switch } from '../../../components/Switch';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../../components/TableColumnSort';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { HintTooltip } from '../../../components/HintTooltip';
import { toast } from '../../../stores/uiStore';
import { formatTime, truncate } from '../../../utils/format';
import { matchExprToEditorString } from '../../../utils/matchExpr';

const GIB = 1024 ** 3;
const TIER_GIB = [1, 5, 10, 20, 50, 100] as const;
const QUOTA_UNITS = ['TiB', 'GiB', 'MiB', 'KiB', 'B'] as const;
type QuotaUnit = (typeof QUOTA_UNITS)[number];
const UNIT_FACTOR: Record<QuotaUnit, number> = {
  B: 1,
  KiB: 1024,
  MiB: 1024 ** 2,
  GiB: 1024 ** 3,
  TiB: 1024 ** 4,
};

type WorkspaceQuotaSortField =
  | 'policy_name'
  | 'policy_desc'
  | 'priority'
  | 'match_expr'
  | 'limit_bytes'
  | 'soft_percent'
  | 'hard_percent'
  | 'source_order_num'
  | 'updated_at';

/** 缺省与「默认排序」都是优先级升序，和列表接口一致。 */
const DEFAULT_SORT_FIELD: WorkspaceQuotaSortField = 'priority';

/** 与 workspace_quota_policy 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  policy_name: 128,
  policy_desc: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

interface FormState {
  policyName: string;
  policyDesc: string;
  matchExpr: string;
  priority: number;
  tier: string;
  customAmount: string;
  customUnit: QuotaUnit;
  softPercent: number;
  hardPercent: number;
}

const emptyForm: FormState = {
  policyName: '',
  policyDesc: '',
  matchExpr: '',
  priority: 100,
  tier: '10',
  customAmount: '',
  customUnit: 'GiB',
  softPercent: 80,
  hardPercent: 100,
};

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
      /* 按单条表达式提交 */
    }
  }
  return text;
}

function customOf(bytes: number): { customAmount: string; customUnit: QuotaUnit } {
  for (const unit of QUOTA_UNITS) {
    const factor = UNIT_FACTOR[unit];
    const value = bytes / factor;
    if (value < 1 && unit !== 'B') continue;
    if (Number.isInteger(value)) {
      return { customAmount: String(value), customUnit: unit };
    }
    const rounded = Math.round(value * 100) / 100;
    if (rounded >= 1 && Math.abs(Math.round(rounded * factor) - bytes) <= 1) {
      return { customAmount: String(rounded), customUnit: unit };
    }
  }
  return { customAmount: String(bytes), customUnit: 'B' };
}

function tierOf(bytes: number): Pick<FormState, 'tier' | 'customAmount' | 'customUnit'> {
  if (bytes === -1) {
    return { tier: 'unlimited', customAmount: '', customUnit: 'GiB' };
  }
  if (bytes === 0) {
    return { tier: '0', customAmount: '', customUnit: 'GiB' };
  }
  for (const gib of TIER_GIB) {
    if (bytes === gib * GIB) {
      return { tier: String(gib), customAmount: '', customUnit: 'GiB' };
    }
  }
  return { tier: 'custom', ...customOf(bytes) };
}

function limitBytesOf(form: FormState): number | null {
  if (form.tier === 'unlimited') return -1;
  if (form.tier === '0') return 0;
  if (form.tier !== 'custom') {
    const gib = Number(form.tier);
    if (!Number.isFinite(gib) || gib <= 0) return null;
    return gib * GIB;
  }
  const amount = Number(form.customAmount);
  if (!Number.isFinite(amount) || amount < 0) return null;
  if (amount === 0) return 0;
  const bytes = Math.round(amount * UNIT_FACTOR[form.customUnit]);
  return bytes >= 0 ? bytes : null;
}

function formatQuota(bytes: number, unlimitedLabel: string): string {
  if (bytes === -1) return unlimitedLabel;
  if (bytes === 0) return '0 B';
  const gib = bytes / GIB;
  if (gib >= 1 && Math.abs(gib - Math.round(gib)) < 1e-6) return `${Math.round(gib)} GiB`;
  if (gib >= 0.01) return `${gib.toFixed(2)} GiB`;
  return `${bytes} B`;
}

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

export function WorkspaceQuotaPanel({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [enabledFilter, setEnabledFilter] = useState('');
  const [sortBy, setSortBy] = useState<WorkspaceQuotaSortField>(DEFAULT_SORT_FIELD);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<WorkspaceQuotaPolicy | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [delTarget, setDelTarget] = useState<WorkspaceQuotaPolicy | null>(null);
  const [disableTarget, setDisableTarget] = useState<WorkspaceQuotaPolicy | null>(null);
  const [lowerOpen, setLowerOpen] = useState(false);
  const { markClean, isDirty } = useFormDirty(modalOpen);

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleSortChange = (field: WorkspaceQuotaSortField, value: ColumnSortValue) => {
    if (value === '') {
      setSortBy(DEFAULT_SORT_FIELD);
      setSortOrder('asc');
    } else {
      setSortBy(field);
      setSortOrder(value);
    }
    setPage(1);
  };

  const { data, loading, error, reload } = useAsync(
    () =>
      WorkspaceQuotaApi.list(instanceId, {
        page,
        page_size: pageSize,
        enabled: enabledFilter === '' ? undefined : enabledFilter === 'true',
        search: searchQuery,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
    [instanceId, page, pageSize, enabledFilter, searchQuery, sortBy, sortOrder],
  );
  const rows = data?.items ?? [];

  useEffect(() => {
    setPage(1);
  }, [instanceId, searchQuery]);

  useEffect(() => {
    if (!data || loading) return;
    const totalPages = Math.max(1, Math.ceil((data.total ?? 0) / pageSize));
    if (page > totalPages) setPage(totalPages);
  }, [data, loading, page, pageSize]);

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
          ...tierOf(editing.limit_bytes),
          softPercent: editing.soft_percent,
          hardPercent: editing.hard_percent,
        }
      : emptyForm;
    setForm(next);
    markClean(next);
  }, [modalOpen, editing, markClean]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const bytesPreview = limitBytesOf(form);

  const save = async () => {
    const policyName = form.policyName.trim();
    if (!policyName) {
      toast('warn', t('instanceDetail.workspaceQuota.fieldRequired', {
        field: t('instanceDetail.workspaceQuota.policyName'),
      }));
      return;
    }
    const limitBytes = limitBytesOf(form);
    if (limitBytes == null || (limitBytes < 0 && limitBytes !== -1)) {
      toast('warn', t('instanceDetail.workspaceQuota.invalidLimit'));
      return;
    }
    if (!Number.isInteger(form.priority)) {
      toast('warn', t('instanceDetail.workspaceQuota.invalidPriority'));
      return;
    }
    if (
      !Number.isInteger(form.softPercent)
      || !Number.isInteger(form.hardPercent)
      || form.softPercent < 0
      || form.hardPercent < 0
      || form.hardPercent <= form.softPercent
    ) {
      toast('warn', t('instanceDetail.workspaceQuota.invalidPercent'));
      return;
    }
    const policyDesc = form.policyDesc.trim() || null;
    setSaving(true);
    try {
      if (editing) {
        await WorkspaceQuotaApi.update(instanceId, editing.policy_id, {
          policy_name: policyName,
          policy_desc: policyDesc,
          match_expr: editorToMatchExpr(form.matchExpr),
          priority: form.priority,
          limit_bytes: limitBytes,
          soft_percent: form.softPercent,
          hard_percent: form.hardPercent,
        });
      } else {
        await WorkspaceQuotaApi.create(instanceId, {
          policy_name: policyName,
          policy_desc: policyDesc,
          match_expr: editorToMatchExpr(form.matchExpr),
          priority: form.priority,
          limit_bytes: limitBytes,
          soft_percent: form.softPercent,
          hard_percent: form.hardPercent,
        });
      }
      toast('success', t('success.saved'));
      setModalOpen(false);
      setLowerOpen(false);
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    const limitBytes = limitBytesOf(form);
    if (editing && limitBytes != null) {
      const wasUnlimited = editing.limit_bytes === -1;
      const isUnlimited = limitBytes === -1;
      // 无限制 → 有限，或有限下调：二次确认
      if (!isUnlimited && (wasUnlimited || limitBytes < editing.limit_bytes)) {
        setLowerOpen(true);
        return;
      }
    }
    void save();
  };

  const patchEnabled = async (row: WorkspaceQuotaPolicy, enabled: boolean) => {
    setTogglingId(row.policy_id);
    try {
      await WorkspaceQuotaApi.update(instanceId, row.policy_id, { enabled });
      toast('success', t('success.saved'));
      await reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setTogglingId(null);
    }
  };

  const remove = async (row: WorkspaceQuotaPolicy) => {
    try {
      await WorkspaceQuotaApi.remove(instanceId, row.policy_id);
      toast('success', t('success.deleted'));
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
              {t('instanceDetail.workspaceQuota.title')}
            </div>
            <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                placeholder={t('instanceDetail.workspaceQuota.searchPlaceholder')}
                className="basis-full sm:basis-auto"
              />
              <button className="btn sm" onClick={() => void reload()}>
                {t('common.refresh')}
              </button>
              <button
                className="btn primary sm"
                onClick={() => {
                  setEditing(null);
                  setModalOpen(true);
                }}
              >
                + {t('instanceDetail.workspaceQuota.new')}
              </button>
            </div>
          </div>
          <div className="page-subtitle truncate pl-3">{t('instanceDetail.workspaceQuota.subtitle')}</div>
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
                        label={t('instanceDetail.workspaceQuota.policyName')}
                        value={sortBy === 'policy_name' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('policy_name', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceDetail.workspaceQuota.policyDesc')}
                        value={sortBy === 'policy_desc' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('policy_desc', value)}
                      />
                    </th>
                    <th>
                      <div className="th-filter">
                        <span className="th-filter__label inline-flex items-center gap-1">
                          {t('instanceDetail.workspaceQuota.priority')}
                          <HintTooltip text={t('instanceDetail.workspaceQuota.priorityHint')} />
                        </span>
                        <TableColumnSort
                          iconOnly
                          label={t('instanceDetail.workspaceQuota.priority')}
                          value={sortBy === 'priority' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('priority', value)}
                        />
                      </div>
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceDetail.workspaceQuota.matchExpr')}
                        value={sortBy === 'match_expr' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('match_expr', value)}
                      />
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceDetail.workspaceQuota.limit')}
                        value={sortBy === 'limit_bytes' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('limit_bytes', value)}
                      />
                    </th>
                    <th>
                      <div className="th-filter">
                        <span className="th-filter__label inline-flex items-center gap-1">
                          {t('instanceDetail.workspaceQuota.softPercent')}
                          <HintTooltip text={t('instanceDetail.workspaceQuota.softPercentHint')} />
                        </span>
                        <TableColumnSort
                          iconOnly
                          label={t('instanceDetail.workspaceQuota.softPercent')}
                          value={sortBy === 'soft_percent' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('soft_percent', value)}
                        />
                      </div>
                    </th>
                    <th>
                      <div className="th-filter">
                        <span className="th-filter__label inline-flex items-center gap-1">
                          {t('instanceDetail.workspaceQuota.hardPercent')}
                          <HintTooltip text={t('instanceDetail.workspaceQuota.hardPercentHint')} />
                        </span>
                        <TableColumnSort
                          iconOnly
                          label={t('instanceDetail.workspaceQuota.hardPercent')}
                          value={sortBy === 'hard_percent' ? sortOrder : ''}
                          options={sortOptions}
                          onChange={(value) => handleSortChange('hard_percent', value)}
                        />
                      </div>
                    </th>
                    <th>
                      <TableColumnSort
                        label={t('instanceDetail.workspaceQuota.sourceOrder')}
                        value={sortBy === 'source_order_num' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('source_order_num', value)}
                      />
                    </th>
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
                        label={t('instanceDetail.workspaceQuota.updatedAt')}
                        value={sortBy === 'updated_at' ? sortOrder : ''}
                        options={sortOptions}
                        onChange={(value) => handleSortChange('updated_at', value)}
                      />
                    </th>
                    <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={11}>
                        <Empty text={t('common.empty')} />
                      </td>
                    </tr>
                  ) : (
                    rows.map((row) => {
                      const full = isFullMatch(row.match_expr);
                      const scope = full
                        ? t('instanceDetail.workspaceQuota.fullMatch')
                        : matchExprToEditorString(
                            Array.isArray(row.match_expr) || typeof row.match_expr === 'string'
                              ? row.match_expr
                              : '',
                          );
                      return (
                        <tr key={row.policy_id}>
                          <td className="align-top">
                            <div className="text-text-strong font-medium break-words">
                              {row.policy_name || '—'}
                            </div>
                            <div className="text-[11px] text-muted mono break-all" title={row.policy_id}>
                              {row.policy_id}
                            </div>
                          </td>
                          <td
                            className="text-[11px] text-muted max-w-[14rem]"
                            title={row.policy_desc ?? undefined}
                          >
                            {row.policy_desc ? truncate(row.policy_desc, 48) : '—'}
                          </td>
                          <td className="whitespace-nowrap">
                            <span className="pill accent mono text-[11px] tabular-nums">{row.priority}</span>
                          </td>
                          <td className="mono text-[11px] max-w-[18rem] break-all" title={scope}>
                            {full ? scope : truncate(scope, 64)}
                          </td>
                          <td className="whitespace-nowrap">
                            {formatQuota(
                              row.limit_bytes,
                              t('instanceDetail.workspaceQuota.unlimited'),
                            )}
                          </td>
                          <td className="whitespace-nowrap tabular-nums">{row.soft_percent}</td>
                          <td className="whitespace-nowrap tabular-nums">{row.hard_percent}</td>
                          <td className="mono text-[11px] whitespace-nowrap">
                            {row.source_order_num || t('instanceDetail.workspaceQuota.sourceManual')}
                          </td>
                          <td className="whitespace-nowrap">
                            <Switch
                              checked={row.enabled}
                              disabled={togglingId === row.policy_id}
                              aria-label={row.enabled ? t('common.enabled') : t('common.disabled')}
                              onChange={(enabled) => {
                                if (!enabled) {
                                  setDisableTarget(row);
                                  return;
                                }
                                void patchEnabled(row, true);
                              }}
                            />
                          </td>
                          <td className="mono text-[11px] text-muted whitespace-nowrap">
                            {formatTime(row.updated_at)}
                          </td>
                          <td className="whitespace-nowrap min-w-[9.5rem]">
                            <div className="flex items-center gap-1">
                              <button
                                className="btn sm ghost"
                                onClick={() => {
                                  setEditing(row);
                                  setModalOpen(true);
                                }}
                              >
                                {t('common.edit')}
                              </button>
                              <button className="btn sm danger" onClick={() => setDelTarget(row)}>
                                {t('common.delete')}
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })
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
            total={data.total ?? rows.length}
            loading={loading}
            error={error}
            onChange={(nextPage, nextPageSize) => {
              setPage(nextPage);
              setPageSize(nextPageSize);
            }}
          />
        )}
      </div>

      <Modal
        open={modalOpen}
        title={editing ? t('instanceDetail.workspaceQuota.edit') : t('instanceDetail.workspaceQuota.new')}
        onClose={() => setModalOpen(false)}
        dirty={isDirty(form)}
        size="lg"
        footer={
          <>
            <ModalCancelButton />
            <button className="btn primary" onClick={requestSave} disabled={saving}>
              {saving ? t('common.loading') : t('common.save')}
            </button>
          </>
        }
      >
        <div className="grid grid-cols-1 gap-3">
          <div className="grid grid-cols-1 md:grid-cols-[3fr_1fr] gap-3">
            <div className="min-w-0">
              <FieldLabel required>{t('instanceDetail.workspaceQuota.policyName')}</FieldLabel>
              <LimitedTextInput
                value={form.policyName}
                maxLength={FIELD_MAX_LENGTH.policy_name}
                onChange={(value) => update('policyName', value)}
              />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.workspaceQuota.priority')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.workspaceQuota.priorityHint')} />
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
            <FieldLabel>{t('instanceDetail.workspaceQuota.policyDesc')}</FieldLabel>
            <LimitedTextInput
              value={form.policyDesc}
              maxLength={FIELD_MAX_LENGTH.policy_desc}
              onChange={(value) => update('policyDesc', value)}
            />
          </div>
          <div>
            <div className="flex items-center gap-1">
              <FieldLabel required>{t('instanceDetail.workspaceQuota.matchExpr')}</FieldLabel>
            </div>
            <MatchExprEditor value={form.matchExpr} onChange={(value) => update('matchExpr', value)} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <FieldLabel required>{t('instanceDetail.workspaceQuota.tier')}</FieldLabel>
              <select
                className="select w-full"
                value={form.tier}
                onChange={(e) => update('tier', e.target.value)}
              >
                <option value="unlimited">{t('instanceDetail.workspaceQuota.tierUnlimited')}</option>
                <option value="0">{t('instanceDetail.workspaceQuota.tierZero')}</option>
                {TIER_GIB.map((gib) => (
                  <option key={gib} value={String(gib)}>
                    {gib} GiB
                  </option>
                ))}
                <option value="custom">{t('instanceDetail.workspaceQuota.tierCustom')}</option>
              </select>
            </div>
            <div>
              <FieldLabel required={form.tier === 'custom'}>
                {t('instanceDetail.workspaceQuota.customSize')}
              </FieldLabel>
              <div className="flex gap-2">
                <input
                  className="input min-w-0 flex-1"
                  type="number"
                  min={0}
                  step="any"
                  disabled={form.tier !== 'custom'}
                  value={form.tier === 'custom' ? form.customAmount : form.tier === 'unlimited' || form.tier === '0' ? '' : form.tier}
                  onChange={(e) => update('customAmount', e.target.value)}
                />
                <select
                  className="select w-24 shrink-0"
                  aria-label={t('instanceDetail.workspaceQuota.customUnit')}
                  disabled={form.tier !== 'custom'}
                  value={form.tier === 'custom' ? form.customUnit : 'GiB'}
                  onChange={(e) => update('customUnit', e.target.value as QuotaUnit)}
                >
                  {QUOTA_UNITS.map((unit) => (
                    <option key={unit} value={unit}>
                      {unit}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
          <div className="text-[11px] text-muted">
            {form.tier === 'unlimited'
              ? t('instanceDetail.workspaceQuota.bytesHintUnlimited')
              : t('instanceDetail.workspaceQuota.bytesHint', {
                  bytes: bytesPreview == null ? '—' : bytesPreview.toLocaleString(),
                })}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.workspaceQuota.softPercent')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.workspaceQuota.softPercentHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                step={1}
                min={0}
                value={Number.isInteger(form.softPercent) ? form.softPercent : ''}
                onChange={(e) => update('softPercent', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
            <div>
              <div className="flex items-center gap-1">
                <FieldLabel required>{t('instanceDetail.workspaceQuota.hardPercent')}</FieldLabel>
                <HintTooltip text={t('instanceDetail.workspaceQuota.hardPercentHint')} />
              </div>
              <input
                className="input w-full"
                type="number"
                step={1}
                min={0}
                value={Number.isInteger(form.hardPercent) ? form.hardPercent : ''}
                onChange={(e) => update('hardPercent', e.target.value === '' ? Number.NaN : Number(e.target.value))}
              />
            </div>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={lowerOpen}
        title={t('instanceDetail.workspaceQuota.lowerTitle')}
        message={t('instanceDetail.workspaceQuota.lowerMessage')}
        danger
        onClose={() => setLowerOpen(false)}
        onConfirm={() => void save()}
      />
      <ConfirmDialog
        open={disableTarget != null}
        title={t('instanceDetail.workspaceQuota.disableTitle')}
        message={t('instanceDetail.workspaceQuota.disableMessage')}
        danger
        onClose={() => setDisableTarget(null)}
        onConfirm={() => {
          if (disableTarget) void patchEnabled(disableTarget, false);
        }}
      />
      <ConfirmDialog
        open={delTarget != null}
        title={t('instanceDetail.workspaceQuota.deleteTitle')}
        message={t('instanceDetail.workspaceQuota.deleteMessage')}
        danger
        onClose={() => setDelTarget(null)}
        onConfirm={() => {
          if (delTarget) void remove(delTarget);
        }}
      />
    </>
  );
}
