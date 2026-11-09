import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from '../../components/Empty';
import { ListSearchInput } from '../../components/ListSearchInput';
import { StatusBadge } from '../../components/StatusBadge';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { ApprovalApi, type ApprovalOrder, type ApprovalSkillUploadApplyData } from '../../services/api';
import { formatTime } from '../../utils/format';
import { WorkspaceNote } from '../../components/WorkspaceNote';

function approvalTypeLabel(
  t: ReturnType<typeof useTranslation>['t'],
  businessType: string,
): string {
  if (businessType === 'workspace_quota_expand') {
    return t('approvals.types.workspace_quota_expand');
  }
  if (businessType === 'skill_upload') {
    return t('approvals.types.skill_upload');
  }
  return businessType;
}

/** 标题由 manager 落库，技能上传的前缀在这里按当前语言重拼，技能名保持原样。 */
function approvalTitle(
  t: ReturnType<typeof useTranslation>['t'],
  order: { business_type: string; title: string; apply_data?: unknown },
): string {
  const raw = (order.title || '').trim();
  if (order.business_type === 'skill_upload' && order.apply_data) {
    const skillName = String(
      (order.apply_data as ApprovalSkillUploadApplyData).skill_name || '',
    ).trim();
    if (skillName) return t('approvals.skillUploadTitle', { name: skillName });
  }
  return raw;
}

export function ApprovalPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [businessType, setBusinessType] = useState('');
  const [applicant, setApplicant] = useState('');
  const [status, setStatus] = useState<ApprovalOrder['status'] | ''>('');
  const [createdAtSort, setCreatedAtSort] = useState<ColumnSortValue>('');
  const { data, loading, error, reload } = useAsync(
    () => ApprovalApi.list({ view: 'todo', search: searchQuery }),
    [searchQuery],
  );
  const allItems = data?.items ?? [];

  const businessTypeOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    ...[...new Set(allItems.map((order) => order.business_type))]
      .sort()
      .map((value) => ({ value, label: approvalTypeLabel(t, value) })),
  ], [allItems, t]);
  const applicantOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    ...[...new Set(allItems.map((order) => order.applicant_id))]
      .sort()
      .map((value) => ({ value, label: value })),
  ], [allItems, t]);
  const statusOptions = useMemo(() => [
    { value: '', label: t('common.all') },
    { value: 'pending', label: t('approvals.status.pending') },
    { value: 'approved', label: t('approvals.status.approved') },
    { value: 'rejected', label: t('approvals.status.rejected') },
    { value: 'cancelled', label: t('approvals.status.cancelled') },
    { value: 'expired', label: t('approvals.status.expired') },
  ], [t]);
  const sortOptions = useMemo(() => [
    { value: 'asc' as const, label: t('common.sortAsc') },
    { value: 'desc' as const, label: t('common.sortDesc') },
    { value: '' as const, label: t('common.sortDefault') },
  ], [t]);

  const items = useMemo(() => {
    const filtered = allItems.filter((order) => {
      if (businessType && order.business_type !== businessType) return false;
      if (applicant && order.applicant_id !== applicant) return false;
      if (status && order.status !== status) return false;
      return true;
    });
    if (!createdAtSort) return filtered;
    return [...filtered].sort((left, right) => {
      const leftTime = left.created_at ? Date.parse(left.created_at) : 0;
      const rightTime = right.created_at ? Date.parse(right.created_at) : 0;
      return createdAtSort === 'asc' ? leftTime - rightTime : rightTime - leftTime;
    });
  }, [allItems, applicant, businessType, createdAtSort, status]);

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-[12rem]">
          <div className="page-title">{t('approvals.title')}</div>
          <div className="page-subtitle">{t('approvals.subtitle')}</div>
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder={t('approvals.searchPlaceholder')}
            className="basis-full sm:basis-auto"
          />
          <button className="btn sm" onClick={() => void reload()}>{t('common.refresh')}</button>
        </div>
      </div>

      <div className="card manager-page-table !p-0">
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
                    <TableColumnFilter
                      label={t('approvals.businessType')}
                      value={businessType}
                      options={businessTypeOptions}
                      onChange={setBusinessType}
                    />
                  </th>
                  <th>{t('approvals.orderNum')}</th>
                  <th>{t('approvals.subjectTitle')}</th>
                  <th>
                    <TableColumnFilter
                      label={t('approvals.applicant')}
                      value={applicant}
                      options={applicantOptions}
                      onChange={setApplicant}
                    />
                  </th>
                  <th>
                    <TableColumnFilter
                      label={t('iam.status')}
                      value={status}
                      options={statusOptions}
                      onChange={(value) => setStatus(value as ApprovalOrder['status'] | '')}
                    />
                  </th>
                  <th>
                    <TableColumnSort
                      label={t('approvals.createdAt')}
                      value={createdAtSort}
                      options={sortOptions}
                      onChange={setCreatedAtSort}
                    />
                  </th>
                  <th>{t('approvals.reason')}</th>
                  <th>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr><td colSpan={8}><Empty text={t('common.empty')} /></td></tr>
                ) : items.map((order) => {
                  const reason = (order.reason || '').trim() || '—';
                  const title = approvalTitle(t, order) || '—';
                  return (
                    <tr key={order.order_num}>
                      <td>
                        <span className="badge">{approvalTypeLabel(t, order.business_type)}</span>
                      </td>
                      <td className="mono text-[11px] text-muted min-w-[16rem] max-w-[22rem] break-all select-all">
                        {order.order_num}
                      </td>
                      <td className="max-w-[16rem]" title={title}>
                        <div className="line-clamp-2">{title}</div>
                      </td>
                      <td className="mono text-[11px] break-all">{order.applicant_id}</td>
                      <td>
                        <StatusBadge
                          status={order.status}
                          label={t(`approvals.status.${order.status}`)}
                        />
                      </td>
                      <td className="mono text-[11px] text-muted whitespace-nowrap">
                        {formatTime(order.created_at)}
                      </td>
                      <td className="max-w-[20rem]" title={reason}>
                        <div className="line-clamp-2 text-sm">{reason}</div>
                      </td>
                      <td>
                        <button
                          className="btn sm"
                          onClick={() => navigate(`/approvals/${encodeURIComponent(order.order_num)}`)}
                        >
                          {t('common.detail')}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <WorkspaceNote title="审批只处理需要人工确认的变更">
        审批列表展示配额申请等需要人工确认的操作。批准后系统还会同步到对应集群，详情页中的同步状态才代表最终生效结果。
      </WorkspaceNote>
    </div>
  );
}
