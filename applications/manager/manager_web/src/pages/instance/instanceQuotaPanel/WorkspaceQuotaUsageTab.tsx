import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from '../../../components/Empty';
import { HintTooltip } from '../../../components/HintTooltip';
import { ListSearchInput } from '../../../components/ListSearchInput';
import { Pagination } from '../../../components/Pagination';
import { TableColumnFilter } from '../../../components/TableColumnFilter';
import { useAsync } from '../../../hooks/useAsync';
import { useListSearch } from '../../../hooks/useListSearch';
import { WorkspaceQuotaApi, isNoOrgGroupId } from '../../../services/api';
import type { WorkspaceQuotaUsage, WorkspaceQuotaUsageStatus } from '../../../types';
import { formatTime } from '../../../utils/format';

const STATUS_PILL: Record<WorkspaceQuotaUsageStatus, string> = {
  ok: 'ok',
  warn: 'warn',
  block: 'danger',
};

function statusLabel(
  t: ReturnType<typeof useTranslation>['t'],
  status: WorkspaceQuotaUsageStatus,
): string {
  if (status === 'warn') return t('instanceDetail.workspaceQuota.usage.statusWarn');
  if (status === 'block') return t('instanceDetail.workspaceQuota.usage.statusBlock');
  return t('instanceDetail.workspaceQuota.usage.statusOk');
}

function formatBytes(value: number | undefined | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value < 0) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function usagePercent(used: number, limit: number): number | null {
  if (limit < 0) return null;
  if (limit === 0) return used > 0 ? 100 : 0;
  return Math.min(999, Math.round((used / limit) * 1000) / 10);
}

function StatusDonut({
  ok,
  warn,
  block,
  emptyLabel,
  totalLabel,
  okLabel,
  warnLabel,
  blockLabel,
}: {
  ok: number;
  warn: number;
  block: number;
  emptyLabel: string;
  totalLabel: string;
  okLabel: string;
  warnLabel: string;
  blockLabel: string;
}) {
  const total = ok + warn + block;
  if (total <= 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted">{emptyLabel}</div>
    );
  }
  const r = 54;
  const c = 2 * Math.PI * r;
  const segs = [
    { value: ok, color: 'var(--ok)' },
    { value: warn, color: 'var(--warn)' },
    { value: block, color: 'var(--danger)' },
  ];
  let offset = 0;
  return (
    <div className="flex h-40 items-center justify-center gap-6">
      <svg width="140" height="140" viewBox="0 0 140 140" aria-hidden="true">
        <g transform="translate(70,70) rotate(-90)">
          {segs.map((seg) => {
            if (seg.value <= 0) return null;
            const len = (seg.value / total) * c;
            const node = (
              <circle
                key={seg.color}
                r={r}
                fill="none"
                stroke={seg.color}
                strokeWidth="18"
                strokeDasharray={`${len} ${c - len}`}
                strokeDashoffset={-offset}
              />
            );
            offset += len;
            return node;
          })}
        </g>
        <text
          x="70"
          y="66"
          textAnchor="middle"
          className="fill-current text-text-strong"
          style={{ fontSize: 22, fontWeight: 600 }}
        >
          {total}
        </text>
        <text
          x="70"
          y="86"
          textAnchor="middle"
          className="fill-current text-muted"
          style={{ fontSize: 11 }}
        >
          {totalLabel}
        </text>
      </svg>
      <div className="flex flex-col gap-2 text-xs">
        <LegendDot color="var(--ok)" label={`${okLabel} ${ok}`} />
        <LegendDot color="var(--warn)" label={`${warnLabel} ${warn}`} />
        <LegendDot color="var(--danger)" label={`${blockLabel} ${block}`} />
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
      <span className="tabular-nums text-muted">{label}</span>
    </div>
  );
}

function TopUsageBars({
  items,
  emptyLabel,
  noOrgLabel,
}: {
  items: Array<Pick<WorkspaceQuotaUsage, 'user_id' | 'group_id' | 'bot_id' | 'used_bytes' | 'status'>>;
  emptyLabel: string;
  noOrgLabel: string;
}) {
  if (items.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted">{emptyLabel}</div>
    );
  }
  const max = Math.max(...items.map((it) => it.used_bytes), 1);
  return (
    <div className="flex flex-col gap-2 py-1">
      {items.map((it) => {
        const pct =
          it.used_bytes <= 0 ? 0 : Math.max(2, Math.round((it.used_bytes / max) * 100));
        const barClass =
          it.status === 'block' ? 'bg-[var(--danger)]' : it.status === 'warn' ? 'bg-[var(--warn)]' : 'bg-[var(--accent)]';
        const groupLabel = isNoOrgGroupId(it.group_id) ? noOrgLabel : it.group_id;
        const subjectLabel = `${it.user_id} | ${groupLabel} | ${it.bot_id}`;
        return (
          <div key={`${it.user_id}|${it.group_id}|${it.bot_id}`} className="min-w-0">
            <div className="mb-0.5 flex items-center justify-between gap-2 text-[11px]">
              <span className="min-w-0 truncate mono text-muted" title={subjectLabel}>
                {subjectLabel}
              </span>
              <span className="shrink-0 tabular-nums text-text-strong">{formatBytes(it.used_bytes)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded" style={{ background: 'var(--border)' }}>
              <div className={`h-full rounded ${barClass}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function WorkspaceQuotaUsageTab({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState('');
  const { searchInput, setSearchInput, searchQuery } = useListSearch();

  useEffect(() => {
    setPage(1);
  }, [instanceId, searchQuery, statusFilter]);

  const { data, loading, error, reload } = useAsync(
    () =>
      WorkspaceQuotaApi.listUsage(instanceId, {
        page,
        page_size: pageSize,
        status: (statusFilter || undefined) as WorkspaceQuotaUsageStatus | undefined,
        search: searchQuery || undefined,
      }),
    [instanceId, page, pageSize, statusFilter, searchQuery],
  );

  const rows = data?.items ?? [];
  const summary = data?.summary;

  useEffect(() => {
    if (!data || loading) return;
    const totalPages = Math.max(1, Math.ceil((data.total ?? 0) / pageSize));
    if (page > totalPages) setPage(totalPages);
  }, [data, loading, page, pageSize]);

  const statusOptions = useMemo(
    () => [
      { value: '', label: t('common.all') },
      { value: 'ok', label: t('instanceDetail.workspaceQuota.usage.statusOk') },
      { value: 'warn', label: t('instanceDetail.workspaceQuota.usage.statusWarn') },
      { value: 'block', label: t('instanceDetail.workspaceQuota.usage.statusBlock') },
    ],
    [t],
  );

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="min-w-0">
        <div className="page-header mb-0 w-full min-w-0 flex-wrap gap-y-3">
          <div className="page-title min-w-[7.5rem] shrink-0 truncate">
            {t('instanceDetail.workspaceQuota.usage.title')}
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('instanceDetail.workspaceQuota.usage.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button type="button" className="btn sm" onClick={() => void reload()}>
              {t('common.refresh')}
            </button>
          </div>
        </div>
        <div className="page-subtitle truncate pl-3">
          {t('instanceDetail.workspaceQuota.usage.subtitle')}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label={t('instanceDetail.workspaceQuota.usage.subjectCount')}
          hint={t('instanceDetail.workspaceQuota.usage.subjectCountHint')}
          value={summary ? String(summary.subject_count) : '—'}
        />
        <StatCard
          label={t('instanceDetail.workspaceQuota.usage.totalUsed')}
          value={summary ? formatBytes(summary.total_used_bytes) : '—'}
        />
        <StatCard
          label={t('instanceDetail.workspaceQuota.usage.warnCount')}
          value={summary ? String(summary.warn_count) : '—'}
          tone="warn"
        />
        <StatCard
          label={t('instanceDetail.workspaceQuota.usage.blockCount')}
          value={summary ? String(summary.block_count) : '—'}
          tone="danger"
        />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="card">
          <div className="mb-2 text-sm font-medium text-text-strong">
            {t('instanceDetail.workspaceQuota.usage.statusChart')}
          </div>
          <StatusDonut
            ok={summary?.ok_count ?? 0}
            warn={summary?.warn_count ?? 0}
            block={summary?.block_count ?? 0}
            emptyLabel={t('common.empty')}
            totalLabel={t('instanceDetail.workspaceQuota.usage.subjectCount')}
            okLabel={t('instanceDetail.workspaceQuota.usage.statusOk')}
            warnLabel={t('instanceDetail.workspaceQuota.usage.statusWarn')}
            blockLabel={t('instanceDetail.workspaceQuota.usage.statusBlock')}
          />
        </div>
        <div className="card">
          <div className="mb-2 text-sm font-medium text-text-strong">
            {t('instanceDetail.workspaceQuota.usage.topChart')}
          </div>
          <TopUsageBars
            items={summary?.top_items ?? []}
            emptyLabel={t('common.empty')}
            noOrgLabel={t('approvals.noOrg')}
          />
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
                  <th>{t('instanceDetail.workspaceQuota.usage.userId')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.groupId')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.botId')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.used')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.limit')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.percent')}</th>
                  <th>
                    <TableColumnFilter
                      label={t('instanceDetail.workspaceQuota.usage.status')}
                      value={statusFilter}
                      options={statusOptions}
                      onChange={(value) => {
                        setStatusFilter(value);
                        setPage(1);
                      }}
                    />
                  </th>
                  <th>{t('instanceDetail.workspaceQuota.usage.policyId')}</th>
                  <th>{t('instanceDetail.workspaceQuota.usage.reportedAt')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={9}>
                      <Empty text={t('instanceDetail.workspaceQuota.usage.empty')} />
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => {
                    const pct = usagePercent(row.used_bytes, row.limit_bytes);
                    return (
                      <tr key={`${row.user_id}|${row.group_id}|${row.bot_id}`}>
                        <td className="mono text-[12px]">{row.user_id}</td>
                        <td className="mono text-[12px]">{row.group_id || '—'}</td>
                        <td className="mono text-[12px]">{row.bot_id}</td>
                        <td className="whitespace-nowrap tabular-nums">{formatBytes(row.used_bytes)}</td>
                        <td className="whitespace-nowrap tabular-nums">
                          {row.limit_bytes < 0
                            ? t('instanceDetail.workspaceQuota.unlimited')
                            : formatBytes(row.limit_bytes)}
                        </td>
                        <td className="whitespace-nowrap tabular-nums">
                          {pct == null ? '—' : `${pct}%`}
                        </td>
                        <td className="whitespace-nowrap">
                          <span className={`pill sm ${STATUS_PILL[row.status] ?? 'muted'}`}>
                            {statusLabel(t, row.status)}
                          </span>
                        </td>
                        <td className="mono text-[11px] text-muted max-w-[10rem] truncate" title={row.source_policy_id}>
                          {row.source_policy_id || '—'}
                        </td>
                        <td className="mono text-[11px] text-muted whitespace-nowrap">
                          {formatTime(row.reported_at)}
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
  );
}

function StatCard({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: 'warn' | 'danger';
  hint?: string;
}) {
  const valueClass =
    tone === 'danger' ? 'text-[var(--danger)]' : tone === 'warn' ? 'text-[var(--warn)]' : 'text-text-strong';
  return (
    <div className="card !py-3">
      <div className="flex items-center gap-1 text-[11px] text-muted">
        <span>{label}</span>
        {hint ? <HintTooltip text={hint} /> : null}
      </div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}
