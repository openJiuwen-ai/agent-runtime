import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { StatusBadge } from '../../components/StatusBadge';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import {
  ApiError,
  ApprovalApi,
  hasPermission,
  isNoOrgGroupId,
} from '../../services/api';
import { toast } from '../../stores/uiStore';
import { formatTime } from '../../utils/format';

function formatBytes(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.detail : (error as Error).message;
}

function formatApprovalGroup(
  groupId: string | null | undefined,
  noOrgLabel: string,
): string {
  return isNoOrgGroupId(groupId) ? noOrgLabel : String(groupId);
}

function approvalTypeLabel(
  t: ReturnType<typeof useTranslation>['t'],
  businessType: string,
): string {
  if (businessType === 'workspace_quota_expand') {
    return t('approvals.types.workspace_quota_expand');
  }
  return businessType;
}

function MetaItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="approval-sheet__meta-item">
      <dt>{label}</dt>
      <dd className="break-all">{value}</dd>
    </div>
  );
}

export function ApprovalEditPage({ orderNum }: { orderNum: string }) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { user } = useAuth();
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { data, loading, error, reload } = useAsync(
    () => ApprovalApi.get(orderNum),
    [orderNum],
  );

  const canDecide = !!data
    && data.status === 'pending'
    && data.applicant_id !== user?.user_id
    && hasPermission(user, 'approval:act');
  const canRetry = !!data
    && data.status === 'approved'
    && data.result_data?.sync_status === 'failed'
    && data.applicant_id !== user?.user_id
    && hasPermission(user, 'approval:act');
  const canAct = canDecide || canRetry;

  useEffect(() => setComment(''), [orderNum]);

  const act = async (action: 'approve' | 'reject') => {
    setSubmitting(true);
    try {
      await ApprovalApi.act(orderNum, action, comment);
      toast('success', t(action === 'approve' ? 'approvals.approved' : 'approvals.rejected'));
      void reload();
    } catch (actError) {
      toast('danger', t('errors.saveFailed', { detail: errorMessage(actError) }));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  if (error || !data) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header flex items-center gap-3">
          <button
            type="button"
            className="btn ghost sm shrink-0"
            onClick={() => navigate('/approvals')}
            title={t('approvals.backToList')}
          >
            ←
          </button>
          <div className="page-title">{t('approvals.detailTitle', { orderNum })}</div>
        </div>
        <div className="card text-sm text-danger">
          {t('errors.loadFailed', { detail: error })}
        </div>
      </div>
    );
  }

  return (
    <div className="approval-sheet flex min-w-0 flex-col gap-4 overflow-x-auto">
      <div className="page-header flex w-full min-w-0 shrink-0 items-start gap-3">
        <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-2">
          <button
            type="button"
            className="btn ghost sm shrink-0 mt-0.5"
            onClick={() => navigate('/approvals')}
            aria-label={t('approvals.title')}
            title={t('approvals.backToList')}
          >
            ←
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <div className="page-title truncate" title={data.title || approvalTypeLabel(t, data.business_type)}>
                {data.title || approvalTypeLabel(t, data.business_type)}
              </div>
              <StatusBadge
                status={data.status}
                label={t(`approvals.status.${data.status}`)}
              />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
              <span className="mono break-all select-all">{data.order_num}</span>
              <span>{t('approvals.createdAt')}: {formatTime(data.created_at)}</span>
              {data.finished_at && (
                <span>{t('approvals.finishedAt')}: {formatTime(data.finished_at)}</span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="approval-sheet__layout">
        <div className="approval-sheet__main flex min-w-0 flex-col gap-4">
          <section className="card approval-sheet__section">
            <div className="card-header">
              <div className="card-title">{t('approvals.sectionBasic')}</div>
            </div>
            <dl className="approval-sheet__meta">
              <MetaItem
                label={t('approvals.businessType')}
                value={<span className="badge">{approvalTypeLabel(t, data.business_type)}</span>}
              />
              <MetaItem label={t('approvals.applicant')} value={data.applicant_id} />
              <MetaItem label={t('approvals.approver')} value={data.approver_id || '—'} />
              <MetaItem label={t('approvals.cluster')} value={<span className="mono text-[12px]">{data.cluster_id}</span>} />
              <MetaItem
                label={t('approvals.group')}
                value={formatApprovalGroup(data.group_id, t('approvals.noOrg'))}
              />
              <MetaItem label={t('approvals.bot')} value={data.bot_id} />
              <MetaItem
                label={t('approvals.workspace')}
                value={<span className="mono text-[12px]">{data.apply_data.workspace_key}</span>}
              />
            </dl>
          </section>

          <section className="card approval-sheet__section">
            <div className="card-header">
              <div className="card-title">{t('approvals.sectionApply')}</div>
            </div>
            <div className="approval-sheet__quota">
              <div className="approval-sheet__quota-cell">
                <div className="approval-sheet__quota-label">{t('approvals.currentLimit')}</div>
                <div className="approval-sheet__quota-value">
                  {formatBytes(data.apply_data.current_limit_bytes)}
                </div>
              </div>
              <div className="approval-sheet__quota-arrow" aria-hidden="true">→</div>
              <div className="approval-sheet__quota-cell is-request">
                <div className="approval-sheet__quota-label">{t('approvals.requestedLimit')}</div>
                <div className="approval-sheet__quota-value">
                  {formatBytes(data.apply_data.requested_limit_bytes)}
                </div>
              </div>
              <div className="approval-sheet__quota-cell">
                <div className="approval-sheet__quota-label">{t('approvals.used')}</div>
                <div className="approval-sheet__quota-value">
                  {formatBytes(data.apply_data.used_bytes)}
                </div>
                <div className="approval-sheet__quota-hint">
                  {t('approvals.usagePercent', {
                    percent: Number.isFinite(data.apply_data.usage_percent)
                      ? Math.round(data.apply_data.usage_percent)
                      : '—',
                  })}
                </div>
              </div>
            </div>
            <div className="approval-sheet__reason">
              <div className="field-label">{t('approvals.reason')}</div>
              <div className="approval-sheet__reason-body whitespace-pre-wrap">
                {data.reason || '—'}
              </div>
            </div>
          </section>

          {data.result_data && (
            <section className="card approval-sheet__section">
              <div className="card-header">
                <div className="card-title">{t('approvals.effectResult')}</div>
              </div>
              <dl className="approval-sheet__meta">
                <MetaItem
                  label={t('approvals.policyId')}
                  value={<span className="mono text-[12px]">{data.result_data.policy_id}</span>}
                />
                <MetaItem
                  label={t('approvals.effectedLimit')}
                  value={formatBytes(data.result_data.limit_bytes)}
                />
                <MetaItem
                  label={t('approvals.syncStatus')}
                  value={(
                    <span className={data.result_data.sync_status === 'success' ? 'text-success' : 'text-danger'}>
                      {t(`approvals.sync.${data.result_data.sync_status}`)}
                    </span>
                  )}
                />
                {data.result_data.sync_detail && (
                  <MetaItem
                    label={t('approvals.syncDetail')}
                    value={<span className="text-xs text-muted">{data.result_data.sync_detail}</span>}
                  />
                )}
              </dl>
            </section>
          )}

          {canAct && (
            <section className="card approval-sheet__section">
              <div className="card-header">
                <div className="card-title">{t('approvals.sectionDecision')}</div>
              </div>
              <label className="block">
                <span className="field-label">{t('approvals.comment')}</span>
                <textarea
                  className="input w-full min-h-24"
                  maxLength={1024}
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder={t('approvals.commentPlaceholder')}
                />
              </label>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                {canDecide && (
                  <>
                    <button
                      type="button"
                      className="btn danger sm"
                      disabled={submitting}
                      onClick={() => void act('reject')}
                    >
                      {t('approvals.reject')}
                    </button>
                    <button
                      type="button"
                      className="btn primary sm"
                      disabled={submitting}
                      onClick={() => void act('approve')}
                    >
                      {t('approvals.approve')}
                    </button>
                  </>
                )}
                {canRetry && (
                  <button
                    type="button"
                    className="btn primary sm"
                    disabled={submitting}
                    onClick={() => void act('approve')}
                  >
                    {t('approvals.retrySync')}
                  </button>
                )}
              </div>
            </section>
          )}
        </div>

        <aside className="approval-sheet__aside">
          <section className="card approval-sheet__section h-full">
            <div className="card-header">
              <div className="card-title">{t('approvals.records')}</div>
            </div>
            {data.records.length === 0 ? (
              <div className="text-sm text-muted">{t('common.empty')}</div>
            ) : (
              <ol className="approval-sheet__timeline">
                {data.records.map((record) => (
                  <li key={record.record_id} className="approval-sheet__timeline-item">
                    <div className="approval-sheet__timeline-dot" />
                    <div className="approval-sheet__timeline-body">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-sm">{record.operator_id}</span>
                        <span className="badge">{t(`approvals.actions.${record.action}`)}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted mono">
                        {formatTime(record.created_at)}
                      </div>
                      {record.comment && (
                        <div className="mt-1.5 text-sm text-muted whitespace-pre-wrap">
                          {record.comment}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
