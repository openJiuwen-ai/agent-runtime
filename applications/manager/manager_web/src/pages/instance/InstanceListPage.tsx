import { allComponentsConnected } from '../../utils/overview';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ImportExportApi, InstanceApi } from '../../services/api';
import type { InstanceSummary } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ListSearchInput } from '../../components/ListSearchInput';
import {
  TableColumnSort,
  type ColumnSortValue,
} from '../../components/TableColumnSort';
import { TableColumnFilter } from '../../components/TableColumnFilter';
import { formatTime } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { ApiError } from '../../services/api';
import { ImportClusterModal } from './modal/ImportClusterModal';
import { useGuideAutoOpen } from '../../hooks/useGuideAutoOpen';
import { bumpGuideRevision } from '../../stores/guideStore';
import { mockInstance } from '../../config/mockManager';
import { WorkspaceNote } from '../../components/WorkspaceNote';

type ViewMode = 'brief' | 'list';

type InstanceSortField =
  | 'jiuwenclaw_name'
  | 'gateway_status'
  | 'runtime_status'
  | 'user_web_status'
  | 'gateway_last_alive'
  | 'runtime_last_alive'
  | 'user_web_last_alive'
  | 'updated_at';

const VIEW_MODE_STORAGE_KEY = 'claw_manager_instance_view';

const INSTANCE_STATUS_VALUES = ['online', 'pending', 'offline'] as const;

async function exportCluster(instance: InstanceSummary, success: string, failed: string) {
  try {
    const { blob, filename } = await ImportExportApi.exportCluster(instance.jiuwenclaw_id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast('success', success);
  } catch (e) {
    const detail = e instanceof ApiError ? e.detail : (e as Error).message;
    toast('danger', `${failed}${detail}`);
  }
}

function readViewMode(): ViewMode {
  const saved = localStorage.getItem(VIEW_MODE_STORAGE_KEY);
  if (saved === 'list') return 'list';
  if (saved === 'brief' || saved === 'compact') return 'brief';
  return 'brief';
}

function InstanceListTable({
  items,
  onChanged,
  sortBy,
  sortOrder,
  sortOptions,
  onSortChange,
  gatewayStatusFilter,
  onGatewayStatusFilterChange,
  runtimeStatusFilter,
  onRuntimeStatusFilterChange,
  userWebStatusFilter,
  onUserWebStatusFilterChange,
}: {
  items: InstanceSummary[];
  onChanged: () => void;
  sortBy: InstanceSortField | '';
  sortOrder: 'asc' | 'desc';
  sortOptions: { value: ColumnSortValue; label: string }[];
  onSortChange: (field: InstanceSortField, value: ColumnSortValue) => void;
  gatewayStatusFilter: string;
  onGatewayStatusFilterChange: (value: string) => void;
  runtimeStatusFilter: string;
  onRuntimeStatusFilterChange: (value: string) => void;
  userWebStatusFilter: string;
  onUserWebStatusFilterChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [deleteTarget, setDeleteTarget] = useState<InstanceSummary | null>(null);

  const statusFilterOptions = useMemo(
    () => [
      { value: '', label: t('common.all') },
      ...INSTANCE_STATUS_VALUES.map((status) => ({ value: status, label: status })),
    ],
    [t],
  );

  return (
    <>
      <div className="card !p-0">
        <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th className="min-w-[12rem]">
                  <TableColumnSort
                    label={t('topology.instanceName')}
                    value={sortBy === 'jiuwenclaw_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('jiuwenclaw_name', value)}
                  />
                </th>
                <th className="whitespace-nowrap">
                  <div className="th-filter">
                    <span className="th-filter__label">{t('topology.gatewayStatus')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('topology.gatewayStatus')}
                      value={sortBy === 'gateway_status' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => onSortChange('gateway_status', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('topology.gatewayStatus')}
                      value={gatewayStatusFilter}
                      options={statusFilterOptions}
                      onChange={onGatewayStatusFilterChange}
                    />
                  </div>
                </th>
                <th className="whitespace-nowrap">
                  <div className="th-filter">
                    <span className="th-filter__label">{t('topology.runtimeStatus')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('topology.runtimeStatus')}
                      value={sortBy === 'runtime_status' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => onSortChange('runtime_status', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('topology.runtimeStatus')}
                      value={runtimeStatusFilter}
                      options={statusFilterOptions}
                      onChange={onRuntimeStatusFilterChange}
                    />
                  </div>
                </th>
                <th className="whitespace-nowrap">
                  <div className="th-filter">
                    <span className="th-filter__label">{t('topology.userWebStatus')}</span>
                    <TableColumnSort
                      iconOnly
                      label={t('topology.userWebStatus')}
                      value={sortBy === 'user_web_status' ? sortOrder : ''}
                      options={sortOptions}
                      onChange={(value) => onSortChange('user_web_status', value)}
                    />
                    <TableColumnFilter
                      iconOnly
                      label={t('topology.userWebStatus')}
                      value={userWebStatusFilter}
                      options={statusFilterOptions}
                      onChange={onUserWebStatusFilterChange}
                    />
                  </div>
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.gatewayLastAlive')}
                    value={sortBy === 'gateway_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('gateway_last_alive', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.runtimeLastAlive')}
                    value={sortBy === 'runtime_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('runtime_last_alive', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.userWebLastAlive')}
                    value={sortBy === 'user_web_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('user_web_last_alive', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  <TableColumnSort
                    label={t('topology.modifiedAt')}
                    value={sortBy === 'updated_at' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('updated_at', value)}
                  />
                </th>
                <th className="whitespace-nowrap min-w-[9.5rem]">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={9}>
                    <Empty text={t('common.empty')} />
                  </td>
                </tr>
              ) : (
                items.map((instance) => (
                  <tr key={instance.jiuwenclaw_id}>
                    <td>
                      <div className="font-medium text-text-strong">{instance.jiuwenclaw_name}</div>
                      <div className="mono text-[11px] text-muted break-all">{instance.jiuwenclaw_id}</div>
                    </td>
                    <td>
                      <StatusBadge status={instance.gateway_status} />
                    </td>
                    <td>
                      <StatusBadge status={instance.runtime_status} />
                    </td>
                    <td>
                      <StatusBadge status={instance.user_web_status || 'pending'} />
                    </td>
                    <td className="mono text-[11px] whitespace-nowrap">
                      {formatTime(instance.gateway_last_alive)}
                    </td>
                    <td className="mono text-[11px] whitespace-nowrap">
                      {formatTime(instance.runtime_last_alive)}
                    </td>
                    <td className="mono text-[11px] whitespace-nowrap">
                      {formatTime(instance.user_web_last_alive)}
                    </td>
                    <td className="mono text-[11px] whitespace-nowrap">{formatTime(instance.updated_at)}</td>
                    <td>
                      <div className="flex items-center gap-1">
                        <button
                          className="btn sm"
                          onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}
                        >
                          {t('redesign.clusterWorkbench', { defaultValue: '配置工作台' })}
                        </button>
                        <button className="btn primary sm" onClick={() => navigate(`/instances/${encodeURIComponent(instance.jiuwenclaw_id)}/setup`)}>{t('redesign.continueClusterSetup', { defaultValue: '继续配置' })} →</button>
                        <button
                          className="btn sm"
                          onClick={() => void exportCluster(
                            instance,
                            t('topology.exportSuccess'),
                            t('topology.exportFailed'),
                          )}
                        >
                          {t('topology.export')}
                        </button>
                        <button className="btn sm danger" onClick={() => setDeleteTarget(instance)}>
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
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        message={t('topology.deleteConfirm')}
        danger
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await InstanceApi.remove(deleteTarget.jiuwenclaw_id);
            toast('success', t('success.deleted'));
            bumpGuideRevision();
            onChanged();
          } catch (e) {
            toast('danger', t('errors.deleteFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
          }
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}

function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (mode: ViewMode) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="tabs-bar" role="group" aria-label={t('topology.viewBrief')}>
      <button
        type="button"
        className={`tab ${value === 'brief' ? 'active' : ''}`}
        title={t('topology.viewBrief')}
        aria-pressed={value === 'brief'}
        onClick={() => onChange('brief')}
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 5h7v7H4V5zm9 0h7v7h-7V5zM4 14h7v7H4v-7zm9 0h7v7h-7v-7z" />
        </svg>
      </button>
      <button
        type="button"
        className={`tab ${value === 'list' ? 'active' : ''}`}
        title={t('topology.viewList')}
        aria-pressed={value === 'list'}
        onClick={() => onChange('list')}
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
        </svg>
      </button>
    </div>
  );
}

export function InstanceListPage() {
  const { navigate } = useRouter();
  const { t } = useTranslation();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [gatewayStatusFilter, setGatewayStatusFilter] = useState<string>('');
  const [runtimeStatusFilter, setRuntimeStatusFilter] = useState<string>('');
  const [userWebStatusFilter, setUserWebStatusFilter] = useState<string>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sortBy, setSortBy] = useState<InstanceSortField | ''>('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [viewMode, setViewMode] = useState<ViewMode>(() => readViewMode());
  const [importOpen, setImportOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  /** 引导跳转：其他页面点「未创建集群」跳过来时自动打开新建弹框 */
  useGuideAutoOpen('instanceCreate', () => navigate('/configuration-wizard?from=cluster'));

  const sortOptions = useMemo(
    () => [
      { value: 'asc' as const, label: t('common.sortAsc') },
      { value: 'desc' as const, label: t('common.sortDesc') },
      { value: '' as const, label: t('common.sortDefault') },
    ],
    [t],
  );

  const handleGatewayStatusFilterChange = (value: string) => {
    setGatewayStatusFilter(value);
    setPage(1);
  };

  const handleRuntimeStatusFilterChange = (value: string) => {
    setRuntimeStatusFilter(value);
    setPage(1);
  };

  const handleUserWebStatusFilterChange = (value: string) => {
    setUserWebStatusFilter(value);
    setPage(1);
  };

  const handleSortChange = (field: InstanceSortField, value: ColumnSortValue) => {
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

  const apiSortBy = viewMode === 'brief' ? 'updated_at' : sortBy || undefined;
  const apiSortOrder =
    viewMode === 'brief' ? 'desc' : sortBy ? sortOrder : undefined;

  const instances = useAsync(
    () =>
      InstanceApi.list({
        page,
        page_size: pageSize,
        gateway_status: gatewayStatusFilter || undefined,
        runtime_status: runtimeStatusFilter || undefined,
        user_web_status: userWebStatusFilter || undefined,
        search: searchQuery,
        sort_by: apiSortBy,
        sort_order: apiSortOrder,
      }),
    [
      gatewayStatusFilter,
      runtimeStatusFilter,
      userWebStatusFilter,
      page,
      pageSize,
      searchQuery,
      sortBy,
      sortOrder,
      viewMode,
    ],
  );

  const refresh = () => {
    void instances.reload();
  };


  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      try {
        await InstanceApi.probeHealth();
      } catch (e) {
        toast(
          'warn',
          t('topology.probeFailed', {
            detail: e instanceof ApiError ? e.detail : (e as Error).message,
          }),
        );
      }
      await instances.reload();
    } finally {
      setRefreshing(false);
    }
  };

  const handleViewModeChange = (mode: ViewMode) => {
    setViewMode(mode);
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode);
  };

  // Mock 集群用于没有后端实例时预览完整的集群配置工作台。
  // 它只出现在卡片视图，不混入真实实例列表、分页和统计接口结果。
  const renderClusterCard = (instance: InstanceSummary, demo = false) => {
    const connected = allComponentsConnected(instance);
    return (
      <article className="card cluster-card" key={instance.jiuwenclaw_id}>
        <div className="cluster-card__head">
          <div className="cluster-card__identity min-w-0">
            <div className="cluster-card__identity-icon" aria-hidden="true">⌁</div>
            <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">{instance.jiuwenclaw_name}</h2>
              {demo && <span className="tag">{t('redesign.mockData', { defaultValue: '本地演示数据' })}</span>}
            </div>
            <p className="mono mt-1 truncate text-xs text-muted">{instance.jiuwenclaw_id} · {instance.namespace || 'default'}</p>
            </div>
          </div>
          <span className={`pill sm ${connected ? 'ok' : 'warn'}`}>{connected ? t('redesign.connected', { defaultValue: '运行正常' }) : t('redesign.inspect', { defaultValue: '待检查' })}</span>
        </div>
        <div className="cluster-card__stats">
          <div><span>{t('redesign.clusterAgents', { defaultValue: 'Agent' })}</span><strong>{demo ? 1 : '—'}</strong></div>
          <div><span>{t('redesign.clusterAdmission', { defaultValue: '准入' })}</span><strong>{demo ? 3 : '—'}</strong></div>
          <div><span>{t('redesign.clusterRuntimes', { defaultValue: '运行池' })}</span><strong>{demo ? 2 : '—'}</strong></div>
        </div>
        {demo && <div className="cluster-card__notice"><span className="statusDot ok" />{t('redesign.mockClusterHint', { defaultValue: '用于预览集群配置工作台，数据不会写入服务端。' })}</div>}
        {!demo && !connected && <div className="cluster-card__notice"><span className="statusDot" />{t('redesign.clusterNeedsInspection', { defaultValue: '部分组件需要检查，进入工作台继续配置。' })}</div>}
        <div className="cluster-card__actions">
          <button type="button" className="btn" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}/cluster-config`)}>{t('redesign.clusterWorkbench', { defaultValue: '配置工作台' })}</button>
          {!demo && <button type="button" className="btn primary" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}/setup`)}>{t('redesign.continueClusterSetup', { defaultValue: '继续配置' })} →</button>}
        </div>
      </article>
    );
  };

  return (
    <>
      <div className="cluster-management-workspace flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('redesign.clusterManagement', { defaultValue: '集群管理' })}>
              {t('redesign.clusterManagement', { defaultValue: '集群管理' })}
            </div>
            <div className="page-subtitle truncate" title={t('redesign.clusterManagementSubtitle', { defaultValue: '服务实例是 Agent 真正运行的环境；进入集群后可继续配置准入、运行资源和 Agent。' })}>
              {t('redesign.clusterManagementSubtitle', { defaultValue: '服务实例是 Agent 真正运行的环境；进入集群后可继续配置准入、运行资源和 Agent。' })}
            </div>
          </div>
        <div className="cluster-management-toolbar">
          <ListSearchInput value={searchInput} onChange={setSearchInput} placeholder={t('topology.searchPlaceholder')} className="min-w-0 flex-1" />
          <ViewModeToggle value={viewMode} onChange={handleViewModeChange} />
          <button className="btn sm" disabled={refreshing} onClick={() => void handleRefresh()}>{refreshing ? t('common.loading') : t('common.refresh')}</button>
          <button className="btn sm" onClick={() => setImportOpen(true)}>{t('topology.import')}</button>
          <button className="btn primary sm" onClick={() => navigate('/configuration-wizard?from=cluster')}>+ {t('redesign.createCluster', { defaultValue: '新建集群' })}</button>
        </div>
        </div>

        <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
          {instances.loading ? (
            <div className="text-sm text-muted">{t('common.loading')}</div>
          ) : instances.error ? (
            <div className="flex flex-col gap-4">
              <div className="card text-sm text-danger">
                {t('errors.loadFailed', { detail: instances.error })}
              </div>
              <div className="cluster-card-grid">
                {renderClusterCard(mockInstance, true)}
              </div>
            </div>
          ) : viewMode === 'list' ? (
            <InstanceListTable
              items={instances.data?.items ?? []}
              onChanged={refresh}
              sortBy={sortBy}
              sortOrder={sortOrder}
              sortOptions={sortOptions}
              onSortChange={handleSortChange}
              gatewayStatusFilter={gatewayStatusFilter}
              onGatewayStatusFilterChange={handleGatewayStatusFilterChange}
              runtimeStatusFilter={runtimeStatusFilter}
              onRuntimeStatusFilterChange={handleRuntimeStatusFilterChange}
              userWebStatusFilter={userWebStatusFilter}
              onUserWebStatusFilterChange={handleUserWebStatusFilterChange}
            />
          ) : (
            <div className="cluster-card-grid">
              {renderClusterCard(mockInstance, true)}
              {(instances.data?.items ?? []).map(it => renderClusterCard(it))}
            </div>
          )}

          {instances.data && (
            <Pagination
              page={page}
              pageSize={pageSize}
              total={instances.data.total ?? instances.data.items.length}
              onChange={(p, ps) => {
                setPage(p);
                setPageSize(ps);
              }}
            />
          )}
          <WorkspaceNote title="服务实例是 Agent 真正运行的环境">
            新建集群从登记连接信息开始，再按“集群准入 → 运行资源 → Agent 绑定 → 状态检查”完成配置；中途离开后点击“继续配置”，会根据已保存的配置进入下一项。
          </WorkspaceNote>
        </div>
      </div>

      <ImportClusterModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => {
          setImportOpen(false);
          bumpGuideRevision();
          refresh();
        }}
      />
    </>
  );
}
