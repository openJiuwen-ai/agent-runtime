import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DeploymentApi, ApiError } from '../../services/api';
import type { DeploymentLogItem, DeploymentTaskDetail } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Empty } from '../../components/Empty';
import { formatTime } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { DeploymentStatusBadge } from './deploymentStatus';

interface Props {
  deploymentId: string;
}

/** 运行中任务元信息轮询间隔 / 日志增量轮询间隔。 */
const META_POLL_INTERVAL_MS = 3000;
const LOG_POLL_INTERVAL_MS = 2000;
/** 距底超过该像素视为用户上滚，暂停自动滚底。 */
const AUTO_SCROLL_THRESHOLD_PX = 40;

/** 时间线固定阶段顺序（与 deploy.sh 阶段一致）。 */
const PHASE_ORDER = ['validate', 'render', 'preflight', 'apply'] as const;

const TERMINAL_STATUSES = new Set(['success', 'failed', 'cancelled', 'interrupted']);

function isTerminal(status?: string | null): boolean {
  return TERMINAL_STATUSES.has(String(status ?? '').toLowerCase());
}

function MetaField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className="mono text-xs break-all">{value}</div>
    </div>
  );
}

/** 阶段时间线项：状态色 start=进行中 spinner / ok=✓ / fail=✗，未开始置灰。 */
function PhaseStep({ phase, state }: { phase: string; state?: string }) {
  const { t } = useTranslation();
  const label = t(`deployments.phase.${phase}`, { defaultValue: phase });
  const normalized = (state ?? '').toLowerCase();
  const reached = !!normalized;
  return (
    <div className="flex items-center gap-2 min-w-0">
      {normalized === 'start' ? (
        <svg className="w-3.5 h-3.5 animate-spin shrink-0 text-info" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
          <path strokeLinecap="round" d="M12 3a9 9 0 109 9" />
        </svg>
      ) : normalized === 'fail' ? (
        <span className="shrink-0 text-danger font-bold" aria-hidden>✗</span>
      ) : normalized === 'ok' ? (
        <span className="shrink-0 text-ok font-bold" aria-hidden>✓</span>
      ) : (
        <span className={`w-3.5 h-3.5 shrink-0 rounded-full border ${reached ? 'border-border-strong' : 'border-border'}`} aria-hidden />
      )}
      <span className={`text-xs truncate ${reached ? 'text-text' : 'text-muted'}`}>{label}</span>
    </div>
  );
}

/** 组件进度行：start=进行中 / ok=✓ / fail=✗ + message。 */
function ComponentRow({
  name,
  index,
  total,
  state,
  message,
}: {
  name: string;
  index: number;
  total: number;
  state: string;
  message?: string;
}) {
  const normalized = state.toLowerCase();
  const current = normalized === 'start';
  return (
    <div className="flex min-w-0 items-start gap-2">
      {current ? (
        <svg className="w-3.5 h-3.5 mt-0.5 shrink-0 animate-spin text-info" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
          <path strokeLinecap="round" d="M12 3a9 9 0 109 9" />
        </svg>
      ) : normalized === 'fail' ? (
        <span className="mt-0.5 shrink-0 text-danger font-bold" aria-hidden>✗</span>
      ) : (
        <span className="mt-0.5 shrink-0 text-ok font-bold" aria-hidden>✓</span>
      )}
      <div className="min-w-0">
        <div className="text-xs truncate">
          <span className="font-medium text-text">{name}</span>
          {total > 0 && (
            <span className="mono text-muted ml-1.5">
              {index}/{total}
            </span>
          )}
        </div>
        {normalized === 'fail' && message && (
          <div className="text-xs text-danger break-all" title={message}>
            {message}
          </div>
        )}
      </div>
    </div>
  );
}

function DeploymentLogViewer({
  deploymentId,
  active,
  truncated,
}: {
  deploymentId: string;
  active: boolean;
  truncated: boolean;
}) {
  const { t } = useTranslation();
  const [lines, setLines] = useState<DeploymentLogItem[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chunkTruncated, setChunkTruncated] = useState(false);
  const lastSeqRef = useRef(0);
  const activeRef = useRef(active);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  // 切换任务时重置游标与缓冲
  useEffect(() => {
    lastSeqRef.current = 0;
    setLines([]);
    setAutoScroll(true);
    setLoadError(null);
    setChunkTruncated(false);
  }, [deploymentId]);

  // 2s 增量拉取：按 seq 游标追加；终态任务补拉一次后停表。
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      try {
        const chunk = await DeploymentApi.listLogs(deploymentId, lastSeqRef.current, 1000);
        if (cancelled) return;
        setLoadError(null);
        if (chunk.items.length > 0) {
          setLines((prev) => [...prev, ...chunk.items]);
        }
        lastSeqRef.current = Math.max(lastSeqRef.current, chunk.last_seq);
        setChunkTruncated(chunk.truncated);
        if (!activeRef.current) {
          window.clearInterval(timer);
        }
      } catch (e) {
        if (!cancelled) {
          setLoadError(e instanceof ApiError ? e.detail : (e as Error).message);
        }
      }
    };
    void tick();
    timer = window.setInterval(() => {
      void tick();
    }, LOG_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [deploymentId]);

  // 新内容到达时自动滚底（用户上滚时暂停，回到底部恢复）
  useEffect(() => {
    const pre = preRef.current;
    if (autoScroll && pre) {
      pre.scrollTop = pre.scrollHeight;
    }
  }, [lines, autoScroll]);

  const handleScroll = () => {
    const pre = preRef.current;
    if (!pre) return;
    const distance = pre.scrollHeight - pre.scrollTop - pre.clientHeight;
    setAutoScroll(distance < AUTO_SCROLL_THRESHOLD_PX);
  };

  return (
    <div className="card !p-0 min-w-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="text-sm font-medium text-text">{t('deployments.logs.title')}</div>
        {active && (
          <span className="pill info sm">
            <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" d="M12 3a9 9 0 109 9" />
            </svg>
            {t('deployments.logs.streaming')}
          </span>
        )}
        {(truncated || chunkTruncated) && (
          <span className="pill warn sm" title={t('deployments.logs.truncatedHint')}>
            {t('deployments.logs.truncated')}
          </span>
        )}
        {!autoScroll && (
          <button className="btn ghost sm ml-auto" onClick={() => setAutoScroll(true)}>
            {t('deployments.logs.followTail')}
          </button>
        )}
      </div>
      {loadError && (
        <div className="px-4 pt-2 text-xs text-danger">
          {t('deployments.logs.loadFailed', { detail: loadError })}
        </div>
      )}
      <pre
        ref={preRef}
        onScroll={handleScroll}
        className="mono text-[11px] leading-relaxed whitespace-pre-wrap break-all px-4 py-3 h-80 overflow-y-auto m-0 text-text"
      >
        {lines.length === 0 && !loadError
          ? t('deployments.logs.empty')
          : lines.map((line) => line.content).join('\n')}
      </pre>
    </div>
  );
}

export function DeploymentDetailPage({ deploymentId }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const task = useAsync(() => DeploymentApi.get(deploymentId), [deploymentId]);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const detail: DeploymentTaskDetail | null = task.data;
  const status = String(detail?.status ?? '').toLowerCase();
  const active = !isTerminal(status);
  const { reload: reloadTask } = task;

  /** 运行中/排队任务每 3s 刷新元信息（终态停止）。 */
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      void reloadTask();
    }, META_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active, reloadTask]);

  if (task.loading && !task.data) {
    return <div className="text-sm text-muted">{t('common.loading')}</div>;
  }
  if (task.error || !detail) {
    return (
      <div className="card text-sm text-danger">
        {t('errors.loadFailed', { detail: task.error ?? deploymentId })}
      </div>
    );
  }

  const progress = detail.progress ?? { phases: {}, components: {}, current: null };
  const components = Object.entries(progress.components ?? {})
    .sort((a, b) => (a[1].index ?? 0) - (b[1].index ?? 0))
    .map(([name, item]) => ({ name, ...item }));
  const interrupted = status === 'interrupted';

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="page-header w-full min-w-0 flex-wrap items-center gap-y-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <button
            type="button"
            className="btn ghost sm shrink-0"
            onClick={() => navigate('/deployments')}
            aria-label={t('deployments.title')}
            title={t('deployments.title')}
          >
            ←
          </button>
          <div className="min-w-0 max-w-full flex-1 sm:max-w-[16rem] sm:flex-none">
            <div className="page-title truncate" title={detail.jiuwenclaw_name}>
              {detail.jiuwenclaw_name}
            </div>
            <div className="text-[11px] text-muted mono truncate" title={deploymentId}>
              {deploymentId}
            </div>
          </div>
          <DeploymentStatusBadge status={detail.status} spinning={status === 'running'} />
          {detail.cancel_requested && (
            <span className="pill warn sm">{t('deployments.cancelRequested')}</span>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <button className="btn sm" disabled={task.loading} onClick={() => void task.reload()}>
            {t('common.refresh')}
          </button>
          {active && (
            <button className="btn danger sm" onClick={() => setConfirmCancel(true)}>
              {t('deployments.actions.cancel')}
            </button>
          )}
        </div>
      </div>

      <div className="card flex flex-col gap-4 min-w-0">
        <div className="grid gap-3 min-w-0 sm:grid-cols-2 lg:grid-cols-4">
          <MetaField label={t('topology.namespace')} value={detail.namespace} />
          <MetaField
            label={t('deployments.columns.deployMode')}
            value={t(`deployments.mode.${detail.deploy_mode}`, { defaultValue: detail.deploy_mode })}
          />
          <MetaField
            label={t('deployments.detail.toolVersion')}
            value={detail.tool_version || '-'}
          />
          <MetaField
            label={t('deployments.detail.exitCode')}
            value={detail.exit_code === null || detail.exit_code === undefined ? '-' : detail.exit_code}
          />
          <MetaField
            label={t('instanceForm.createdBy')}
            value={detail.created_by || '-'}
          />
          <MetaField
            label={t('deployments.columns.createdAt')}
            value={formatTime(detail.created_at)}
          />
          <MetaField
            label={t('deployments.detail.startedAt')}
            value={formatTime(detail.started_at)}
          />
          <MetaField
            label={t('deployments.detail.finishedAt')}
            value={formatTime(detail.finished_at)}
          />
        </div>

        {status === 'failed' && detail.error_summary && (
          <div className="text-xs text-danger break-all whitespace-pre-wrap" role="alert">
            {t('deployments.detail.errorSummary')}：{detail.error_summary}
          </div>
        )}
        {interrupted && (
          <div className="text-xs text-warn break-all" role="alert">
            {t('deployments.detail.interruptedHint')}
          </div>
        )}
        {progress.auto_register && (
          <div className="text-xs text-muted break-all">{progress.auto_register}</div>
        )}
        {status === 'success' && detail.instance_jid && (
          <div>
            <button
              className="btn sm primary"
              onClick={() => navigate(`/instances/${detail.instance_jid}`)}
            >
              {t('deployments.detail.gotoInstance')}
            </button>
          </div>
        )}
      </div>

      <div className="card flex flex-col gap-3 min-w-0">
        <div className="text-sm font-medium text-text">{t('deployments.progress.title')}</div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {PHASE_ORDER.map((phase) => (
            <PhaseStep key={phase} phase={phase} state={progress.phases?.[phase]} />
          ))}
        </div>
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <div className="label">{t('deployments.progress.components')}</div>
          {components.length === 0 ? (
            <Empty text={t('deployments.progress.noComponents')} />
          ) : (
            components.map((item) => (
              <ComponentRow
                key={item.name}
                name={item.name}
                index={item.index}
                total={item.total}
                state={item.state}
                message={item.message}
              />
            ))
          )}
        </div>
      </div>

      <DeploymentLogViewer
        deploymentId={deploymentId}
        active={active}
        truncated={!!progress.log_truncated}
      />

      <ConfirmDialog
        open={confirmCancel}
        message={t('deployments.cancelConfirm', { id: deploymentId })}
        danger
        onConfirm={async () => {
          try {
            await DeploymentApi.cancel(deploymentId);
            toast('success', t('deployments.cancelSuccess'));
            void task.reload();
          } catch (e) {
            toast(
              'danger',
              t('deployments.cancelFailed', {
                detail: e instanceof ApiError ? e.detail : (e as Error).message,
              }),
            );
          }
        }}
        onClose={() => setConfirmCancel(false)}
      />
    </div>
  );
}
