import { useTranslation } from 'react-i18next';

/**
 * 部署任务状态 → 色系映射（与集群在线状态用的 StatusBadge 内置映射不同，
 * 部署语义里 running 是“进行中”而非“健康”，因此单独维护）：
 * success=绿 / failed=红 / running=蓝（spinner）/ pending=灰 / cancelled+interrupted=黄。
 */
export type DeploymentTone = 'ok' | 'danger' | 'info' | 'muted' | 'warn';

export function deploymentTone(status?: string | null): DeploymentTone {
  switch ((status ?? '').toLowerCase()) {
    case 'success':
      return 'ok';
    case 'failed':
      return 'danger';
    case 'running':
      return 'info';
    case 'cancelled':
    case 'interrupted':
      return 'warn';
    default:
      return 'muted';
  }
}

/** 任务/阶段状态徽章：复用全局 pill / statusDot 语义色，不写死颜色。 */
export function DeploymentStatusBadge({
  status,
  label,
  spinning = false,
}: {
  status?: string | null;
  label?: string;
  /** 进行中（running / start）时显示旋转指示 */
  spinning?: boolean;
}) {
  const { t } = useTranslation();
  const tone = deploymentTone(status);
  const dotClass =
    tone === 'ok'
      ? 'statusDot ok'
      : tone === 'danger'
        ? 'statusDot'
        : tone === 'warn'
          ? 'statusDot warn'
          : 'statusDot muted';
  const text =
    label ??
    (status ? t(`deployments.status.${String(status).toLowerCase()}`, { defaultValue: String(status) }) : '-');
  return (
    <span className={`pill ${tone}`} title={status ?? ''}>
      {spinning ? (
        <svg
          className="w-3 h-3 animate-spin"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden
        >
          <path strokeLinecap="round" d="M12 3a9 9 0 109 9" />
        </svg>
      ) : tone === 'info' ? (
        <span className="w-2 h-2 rounded-full bg-info" aria-hidden />
      ) : (
        <span className={dotClass} aria-hidden />
      )}
      <span className="mono text-[11px] uppercase tracking-wider">{text}</span>
    </span>
  );
}
