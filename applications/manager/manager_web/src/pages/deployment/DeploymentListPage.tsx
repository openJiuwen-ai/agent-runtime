import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DeploymentApi, ApiError } from '../../services/api';
import type { DeploymentDefaults, DeploymentTaskSummary } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useListSearch } from '../../hooks/useListSearch';
import { useRouter } from '../../router';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { Pagination } from '../../components/Pagination';
import { ListSearchInput } from '../../components/ListSearchInput';
import { formatTime } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { CreateDeploymentModal } from './CreateDeploymentModal';
import { DeploymentStatusBadge } from './deploymentStatus';

/** 运行中/排队任务存在时整表轮询间隔。 */
const ACTIVE_POLL_INTERVAL_MS = 3000;

function isActiveTask(item: DeploymentTaskSummary): boolean {
  const status = String(item.status ?? '').toLowerCase();
  return status === 'pending' || status === 'running';
}

function DeploymentListTable({
  items,
  onChanged,
}: {
  items: DeploymentTaskSummary[];
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [cancelTarget, setCancelTarget] = useState<DeploymentTaskSummary | null>(null);

  return (
    <>
      <div className="card !p-0">
        <div className="overflow-x-auto">
          <table className="table w-max min-w-full">
            <thead>
              <tr>
                <th className="min-w-[10rem]">{t('deployments.columns.taskId')}</th>
                <th className="min-w-[8rem]">{t('topology.namespace')}</th>
                <th className="min-w-[9rem]">{t('deployments.columns.instanceName')}</th>
                <th className="whitespace-nowrap">{t('deployments.columns.deployMode')}</th>
                <th className="whitespace-nowrap">{t('deployments.columns.result')}</th>
                <th className="whitespace-nowrap">{t('deployments.columns.phase')}</th>
                <th className="whitespace-nowrap">{t('instanceForm.createdBy')}</th>
                <th className="whitespace-nowrap min-w-[10.5rem]">
                  {t('deployments.columns.createdAt')}
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
                items.map((task) => {
                  const active = isActiveTask(task);
                  return (
                    <tr key={task.deployment_id}>
                      <td>
                        <div className="mono text-[11px] text-muted break-all" title={task.deployment_id}>
                          {task.deployment_id}
                        </div>
                      </td>
                      <td className="mono text-[11px]">{task.namespace}</td>
                      <td>
                        <div className="font-medium text-text-strong">{task.jiuwenclaw_name}</div>
                      </td>
                      <td>
                        <span className="tag">{t(`deployments.mode.${task.deploy_mode}`, { defaultValue: task.deploy_mode })}</span>
                      </td>
                      <td>
                        <DeploymentStatusBadge
                          status={task.status}
                          spinning={String(task.status).toLowerCase() === 'running'}
                        />
                      </td>
                      <td>
                        <span className="mono text-[11px] text-muted">
                          {t(`deployments.phase.${task.phase}`, { defaultValue: String(task.phase) })}
                        </span>
                      </td>
                      <td className="mono text-[11px]">{task.created_by || '-'}</td>
                      <td className="mono text-[11px] whitespace-nowrap">{formatTime(task.created_at)}</td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button
                            className="btn sm"
                            onClick={() => navigate(`/deployments/${task.deployment_id}`)}
                          >
                            {t('topology.viewDetail')}
                          </button>
                          {active && (
                            <button
                              className="btn sm danger"
                              onClick={() => setCancelTarget(task)}
                            >
                              {t('deployments.actions.cancel')}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        open={cancelTarget !== null}
        message={t('deployments.cancelConfirm', {
          id: cancelTarget?.deployment_id ?? '',
        })}
        danger
        onConfirm={async () => {
          if (!cancelTarget) return;
          try {
            await DeploymentApi.cancel(cancelTarget.deployment_id);
            toast('success', t('deployments.cancelSuccess'));
            onChanged();
          } catch (e) {
            toast(
              'danger',
              t('deployments.cancelFailed', {
                detail: e instanceof ApiError ? e.detail : (e as Error).message,
              }),
            );
          }
        }}
        onClose={() => setCancelTarget(null)}
      />
    </>
  );
}

export function DeploymentListPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { searchInput, setSearchInput, searchQuery } = useListSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [createOpen, setCreateOpen] = useState(false);

  const defaults = useAsync<DeploymentDefaults>(() => DeploymentApi.getDefaults(), []);
  const tasks = useAsync(
    () =>
      DeploymentApi.list({
        page,
        page_size: pageSize,
        search: searchQuery,
      }),
    [page, pageSize, searchQuery],
  );

  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const items = tasks.data?.items ?? [];
  const hasActive = items.some(isActiveTask);
  const { reload: reloadTasks } = tasks;

  /** 存在运行中/排队任务时整表 3s 轮询（reload 由 useAsync 按 deps 记忆化）。 */
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => {
      void reloadTasks();
    }, ACTIVE_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [hasActive, reloadTasks]);

  const defaultsData = defaults.data;

  return (
    <>
      <div className="flex min-w-0 flex-col gap-4">
        <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
          <div className="min-w-[7.5rem] max-w-[12rem] shrink-0 sm:max-w-[16rem]">
            <div className="page-title truncate" title={t('deployments.title')}>
              {t('deployments.title')}
            </div>
            <div className="page-subtitle truncate" title={t('deployments.subtitle')}>
              {t('deployments.subtitle')}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
            <ListSearchInput
              value={searchInput}
              onChange={setSearchInput}
              placeholder={t('deployments.searchPlaceholder')}
              className="basis-full sm:basis-auto"
            />
            <button className="btn sm" disabled={tasks.loading} onClick={() => void tasks.reload()}>
              {t('common.refresh')}
            </button>
            <button
              className="btn primary sm"
              onClick={() => setCreateOpen(true)}
              disabled={defaultsData ? !defaultsData.deploy_enabled : false}
              title={
                defaultsData && !defaultsData.deploy_enabled
                  ? t('deployments.deployDisabled')
                  : undefined
              }
            >
              + {t('deployments.create')}
            </button>
          </div>
        </div>

        <div className="flex w-full min-w-0 shrink-0 flex-col gap-4">
          {defaultsData && !defaultsData.available && (
            <div className="cost-hint" role="alert">
              <svg className="cost-hint__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
              </svg>
              <span>
                {t('deployments.unavailable')}
                {defaultsData.unavailable_reason ? `：${defaultsData.unavailable_reason}` : ''}
              </span>
            </div>
          )}

          {defaults.error && (
            <div className="text-xs text-muted">{t('deployments.defaultsLoadFailed')}</div>
          )}

          {tasks.loading && !tasks.data ? (
            <div className="text-sm text-muted">{t('common.loading')}</div>
          ) : tasks.error ? (
            <div className="card text-sm text-danger">
              {t('errors.loadFailed', { detail: tasks.error })}
            </div>
          ) : (
            <DeploymentListTable items={items} onChanged={() => void tasks.reload()} />
          )}

          {tasks.data && (
            <Pagination
              page={page}
              pageSize={pageSize}
              total={tasks.data.total ?? tasks.data.items.length}
              onChange={(p, ps) => {
                setPage(p);
                setPageSize(ps);
              }}
            />
          )}
        </div>
      </div>

      <CreateDeploymentModal
        open={createOpen}
        defaults={defaultsData}
        onClose={() => setCreateOpen(false)}
        onCreated={(deploymentId) => {
          setCreateOpen(false);
          void tasks.reload();
          navigate(`/deployments/${deploymentId}`);
        }}
      />
    </>
  );
}
