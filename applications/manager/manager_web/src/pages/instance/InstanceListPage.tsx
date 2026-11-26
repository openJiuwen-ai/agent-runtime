import { allComponentsConnected } from '../../utils/overview';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceApi } from '../../services/api';
import type { InstanceSummary } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { ClusterManagementActions } from '../../components/ClusterManagementActions';
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
import { ApiError, DeploymentApi } from '../../services/api';
import { ImportClusterModal } from './modal/ImportClusterModal';
import { CreateDeploymentModal } from '../deployment/CreateDeploymentModal';
import { useGuideAutoOpen } from '../../hooks/useGuideAutoOpen';
import { bumpGuideRevision } from '../../stores/guideStore';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { ClusterCardStats } from './ClusterCardStats';

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
          <table className="table cluster-list-table w-max min-w-full">
            <thead>
              <tr>
                <th>
                  <TableColumnSort
                    label={t('topology.instanceName')}
                    value={sortBy === 'jiuwenclaw_name' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('jiuwenclaw_name', value)}
                  />
                </th>
                <th>
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
                <th>
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
                <th>
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
                <th>
                  <TableColumnSort
                    label={t('topology.gatewayLastAlive')}
                    value={sortBy === 'gateway_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('gateway_last_alive', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('topology.runtimeLastAlive')}
                    value={sortBy === 'runtime_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('runtime_last_alive', value)}
                  />
                </th>
                <th>
                  <TableColumnSort
                    label={t('topology.userWebLastAlive')}
                    value={sortBy === 'user_web_last_alive' ? sortOrder : ''}
                    options={sortOptions}
                    onChange={(value) => onSortChange('user_web_last_alive', value)}
                  />
                </th>
                <th>
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
                    <td className="align-top">
                      <div className="text-text-strong font-medium break-words" title={instance.jiuwenclaw_name}>
                        {instance.jiuwenclaw_name}
                      </div>
                      <div className="text-[11px] text-muted mono break-all" title={instance.jiuwenclaw_id}>
                        {instance.jiuwenclaw_id}
                      </div>
                    </td>
                    <td className="whitespace-nowrap">
                      <StatusBadge status={instance.gateway_status} />
                    </td>
                    <td className="whitespace-nowrap">
                      <StatusBadge status={instance.runtime_status} />
                    </td>
                    <td className="whitespace-nowrap">
                      <StatusBadge status={instance.user_web_status || 'pending'} />
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap" title={formatTime(instance.gateway_last_alive)}>
                      {formatTime(instance.gateway_last_alive)}
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap" title={formatTime(instance.runtime_last_alive)}>
                      {formatTime(instance.runtime_last_alive)}
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap" title={formatTime(instance.user_web_last_alive)}>
                      {formatTime(instance.user_web_last_alive)}
                    </td>
                    <td className="mono text-[11px] text-muted whitespace-nowrap" title={formatTime(instance.updated_at)}>
                      {formatTime(instance.updated_at)}
                    </td>
                    <td className="whitespace-nowrap min-w-[9.5rem]">
                      <div className="flex items-center gap-1">
                        <button
                          className="btn sm ghost"
                          onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}`)}
                        >
                          {t('redesign.clusterWorkbench')}
                        </button>
                        <button
                          className="btn sm ghost"
                          onClick={() => navigate(`/instances/${encodeURIComponent(instance.jiuwenclaw_id)}/setup`)}
                        >
                          {t('redesign.continueClusterSetup')}
                        </button>
                        <ClusterManagementActions compact instanceId={instance.jiuwenclaw_id} name={instance.jiuwenclaw_name} namespace={instance.namespace} onDeleted={onChanged} />
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
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
  const [deployOpen, setDeployOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const deployDefaults = useAsync(() => DeploymentApi.getDefaults(), []);

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

  const renderClusterCard = (instance: InstanceSummary) => {
    const connected = allComponentsConnected(instance);
    return (
      <article className="card cluster-card" key={instance.jiuwenclaw_id}>
        <div className="cluster-card__head">
          <div className="cluster-card__identity min-w-0">
            <div className="cluster-card__identity-icon" aria-hidden="true">⌁</div>
            <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">{instance.jiuwenclaw_name}</h2>
            </div>
            <p className="mono mt-1 truncate text-xs text-muted">{instance.jiuwenclaw_id} · {instance.namespace || 'default'}</p>
            </div>
          </div>
          <span className={`pill sm ${connected ? 'ok' : 'warn'}`}>{connected ? t('redesign.connected') : t('redesign.inspect')}</span>
        </div>
        <ClusterCardStats instanceId={instance.jiuwenclaw_id} />
        {!connected && <div className="cluster-card__notice"><span className="statusDot" />{t('redesign.clusterNeedsInspection')}</div>}
        <div className="cluster-card__actions">
          <button type="button" className="btn" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}/cluster-config`)}>{t('redesign.clusterWorkbench')}</button>
          <button type="button" className="btn primary" onClick={() => navigate(`/instances/${instance.jiuwenclaw_id}/setup`)}>{t('redesign.continueClusterSetup')}</button>
          <div className="cluster-card__management-actions"><ClusterManagementActions compact size="md" instanceId={instance.jiuwenclaw_id} name={instance.jiuwenclaw_name} namespace={instance.namespace} onDeleted={refresh} /></div>
        </div>
      </article>
    );
  };

  return (
    <>
      <div className="cluster-management-workspace flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
            <div className="page-title truncate" title={t('redesign.clusterManagement')}>
              {t('redesign.clusterManagement')}
            </div>
            <div className="page-subtitle truncate" title={t('redesign.clusterManagementSubtitle')}>
              {t('redesign.clusterManagementSubtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('topology.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <ViewModeToggle value={viewMode} onChange={handleViewModeChange} />
            <button className="btn sm" disabled={refreshing} onClick={() => void handleRefresh()}>
              {refreshing ? t('common.loading') : t('common.refresh')}
            </button>
            <button className="btn sm" onClick={() => setImportOpen(true)}>
              {t('topology.import')}
            </button>
            <button
              className="btn primary sm"
              title={t('deployments.action.upHint')}
              onClick={() => setDeployOpen(true)}
            >
              + {t('topology.deployCluster')}
            </button>
            <button className="btn sm" onClick={() => navigate('/configuration-wizard?from=cluster')}>
              + {t('redesign.createCluster')}
            </button>
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
          ) : !instances.data?.items.length ? (
            <div className="card"><Empty text={t('common.empty')} /></div>
          ) : (
            <div className="cluster-card-grid">
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
          <WorkspaceNote title={t('redesign.workspaceNoteTitle')}>
            {t('redesign.workspaceNoteBody')}
          </WorkspaceNote>
        </div>
      </div>

      <CreateDeploymentModal
        open={deployOpen}
        defaults={deployDefaults.data ?? null}
        onClose={() => setDeployOpen(false)}
        onCreated={(deploymentId) => {
          setDeployOpen(false);
          void navigate(`/deployments/${deploymentId}`);
        }}
      />
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
