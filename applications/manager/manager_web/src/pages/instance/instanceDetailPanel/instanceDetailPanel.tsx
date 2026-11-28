import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatTime, relativeTime } from '../../../utils/format';
import { StatusBadge } from '../../../components/StatusBadge';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import type { InstanceComponentBlock, InstanceComponentStatus, InstanceDetail } from '../../../types';
import { ClusterContentHeading } from '../ClusterContentHeading';
import './ResourceMeter.css';
import './ConnectivityCard.css';

type HostField = 'gateway_host' | 'runtime_host';

interface Props {
  instance: { data?: InstanceDetail | null };
  componentStatus?: InstanceComponentStatus | null;
  componentStatusLoading?: boolean;
  componentStatusError?: string | null;
  onSaveConnectivity: (patch: Partial<Record<HostField, string>>) => Promise<boolean>;
  savingConnectivity?: boolean;
  onRefresh: () => void;
  probing: boolean;
  checkResult: string;
}

const HOST_MAX = 512;

const COMPONENT_LABELS: Record<string, { zh: string; en: string }> = {
  gateway: { zh: 'Gateway', en: 'Gateway' },
  runtime: { zh: 'Runtime', en: 'Runtime' },
  agentserver: { zh: 'AgentServer', en: 'AgentServer' },
};

type MeterTone = 'ok' | 'warn' | 'danger' | 'muted';

function finiteOrNull(n: number): number | null {
  return Number.isFinite(n) ? n : null;
}

function parseCpuToMillis(raw: string): number | null {
  const text = raw.trim();
  if (!text) return null;
  try {
    if (text.endsWith('n')) return finiteOrNull(Math.max(0, Math.floor(Number(text.slice(0, -1)) / 1_000_000)));
    if (text.endsWith('u')) return finiteOrNull(Math.max(0, Math.floor(Number(text.slice(0, -1)) / 1_000)));
    if (text.endsWith('m')) return finiteOrNull(Math.max(0, Number(text.slice(0, -1))));
    if (text.includes('.')) return finiteOrNull(Math.max(0, Math.floor(Number(text) * 1000)));
    return finiteOrNull(Math.max(0, Number(text) * 1000));
  } catch {
    return null;
  }
}

function parseMemoryToBytes(raw: string): number | null {
  const text = raw.trim();
  if (!text) return null;
  const units: Record<string, number> = {
    Ki: 1024,
    Mi: 1024 ** 2,
    Gi: 1024 ** 3,
    Ti: 1024 ** 4,
    K: 1000,
    M: 1000 ** 2,
    G: 1000 ** 3,
    T: 1000 ** 4,
  };
  try {
    for (const [suffix, mult] of Object.entries(units)) {
      if (text.endsWith(suffix)) {
        return finiteOrNull(Math.max(0, Number(text.slice(0, -suffix.length)) * mult));
      }
    }
    if (text.endsWith('i')) return finiteOrNull(Math.max(0, Number(text.slice(0, -1))));
    return finiteOrNull(Math.max(0, Number(text)));
  } catch {
    return null;
  }
}

function usagePercent(usage: string, limit: string): number | null {
  const cpuU = parseCpuToMillis(usage);
  const cpuL = parseCpuToMillis(limit);
  if (cpuU != null && cpuL != null && cpuL > 0) {
    return finiteOrNull(Math.round((cpuU / cpuL) * 1000) / 10);
  }
  const memU = parseMemoryToBytes(usage);
  const memL = parseMemoryToBytes(limit);
  if (memU != null && memL != null && memL > 0) {
    return finiteOrNull(Math.round((memU / memL) * 1000) / 10);
  }
  return null;
}

function meterTone(pct: number | null): MeterTone {
  if (pct == null) return 'muted';
  if (pct >= 90) return 'danger';
  if (pct >= 70) return 'warn';
  return 'ok';
}

function formatPctLabel(pct: number | null): string {
  if (pct == null) return '—';
  const rounded = Math.round(pct);
  return `${rounded}%`;
}

const RING_R = 15.5;
const RING_C = 2 * Math.PI * RING_R;

/** 环形占比：圆心百分比，下方占用/配额 */
function ResourceMeter({
  usage,
  quota,
  zh,
}: {
  usage?: string | null;
  quota?: string | null;
  zh: boolean;
}) {
  const u = (usage || '').trim();
  const q = (quota || '').trim();
  const pct = u && q ? usagePercent(u, q) : null;
  const tone = meterTone(pct);
  const clamped = pct == null ? 0 : Math.min(100, Math.max(0, pct));
  const offset = RING_C * (1 - clamped / 100);

  if (!u && !q) {
    return <span className="text-muted text-xs">—</span>;
  }

  return (
    <div
      className="resource-meter"
      title={
        pct != null
          ? zh
            ? `占用 ${u} / 配额 ${q}（${formatPctLabel(pct)}）`
            : `Usage ${u} / quota ${q} (${formatPctLabel(pct)})`
          : q
            ? zh
              ? `占用 ${u || '—'} / 配额 ${q}`
              : `Usage ${u || '—'} / quota ${q}`
            : zh
              ? `占用 ${u}（未配置配额）`
              : `Usage ${u} (no quota)`
      }
    >
      <div className="resource-meter__ring" aria-hidden>
        <svg className="resource-meter__svg" viewBox="0 0 36 36">
          <circle className="resource-meter__track" cx="18" cy="18" r={RING_R} />
          <circle
            className={`resource-meter__progress is-${tone}`}
            cx="18"
            cy="18"
            r={RING_R}
            strokeDasharray={RING_C}
            strokeDashoffset={offset}
          />
        </svg>
        <span className={`resource-meter__center is-${tone}`}>{formatPctLabel(pct)}</span>
      </div>
      <div className="resource-meter__footer">
        <div className="resource-meter__ratio">
          {q
            ? `${u || '—'} / ${q}`
            : zh
              ? `${u || '—'} / 未配置配额`
              : `${u || '—'} / no quota`}
        </div>
      </div>
    </div>
  );
}

function ComponentCard({
  block,
  zh,
}: {
  block: InstanceComponentBlock;
  zh: boolean;
}) {
  const label = COMPONENT_LABELS[block.type]?.[zh ? 'zh' : 'en'] || block.type;
  return (
    <div className="card flex flex-col gap-3">
      <div className="card-header">
        <div className="card-title flex items-center gap-2">
          <span>{label}</span>
          <StatusBadge status={block.status || 'pending'} />
        </div>
        {block.last_alive && (
          <span className="text-xs text-muted" title={relativeTime(block.last_alive)}>
            {zh ? '最近探活' : 'Last probe'}{' '}
            <span className="mono">{formatTime(block.last_alive)}</span>
          </span>
        )}
      </div>
      {(block.pods_error || block.metrics_error) && (
        <p className="text-xs text-muted m-0" role="status">
          {block.pods_error
            ? (zh ? `Pod 列表：${block.pods_error}` : `Pods: ${block.pods_error}`)
            : null}
          {block.pods_error && block.metrics_error ? ' · ' : null}
          {block.metrics_error && !block.pods_error
            ? (zh ? `用量：${block.metrics_error}` : `Metrics: ${block.metrics_error}`)
            : block.metrics_error && block.pods_error
              ? (zh ? `用量：${block.metrics_error}` : `Metrics: ${block.metrics_error}`)
              : null}
        </p>
      )}
      <div className="table-wrap">
        <table className="table component-status-table">
          <colgroup>
            <col className="component-status-table__col-name" />
            <col className="component-status-table__col-ready" />
            <col className="component-status-table__col-phase" />
            <col className="component-status-table__col-started" />
            <col className="component-status-table__col-cpu" />
            <col className="component-status-table__col-mem" />
            <col className="component-status-table__col-restarts" />
          </colgroup>
          <thead>
            <tr>
              <th>{zh ? 'Pod 名称' : 'Pod'}</th>
              <th>Ready</th>
              <th>{zh ? 'Pod 状态' : 'Pod status'}</th>
              <th>{zh ? '启动时间' : 'Started'}</th>
              <th>CPU</th>
              <th>{zh ? '内存' : 'Memory'}</th>
              <th>{zh ? '重启次数' : 'Restarts'}</th>
            </tr>
          </thead>
          <tbody>
            {block.pods.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-muted text-sm">
                  {block.pods_error
                    ? (zh ? '无法读取 Pod' : 'Unable to list pods')
                    : (zh ? '暂无 Pod' : 'No pods')}
                </td>
              </tr>
            ) : (
              block.pods.map((pod) => (
                <tr key={pod.name}>
                  <td className="mono text-xs break-all">{pod.name}</td>
                  <td>
                    <StatusBadge status={pod.ready ? 'online' : 'offline'} />
                  </td>
                  <td className="mono text-xs">{pod.phase || '—'}</td>
                  <td className="mono text-xs" title={pod.started_at ? relativeTime(pod.started_at) : undefined}>
                    {pod.started_at ? formatTime(pod.started_at) : '—'}
                  </td>
                  <td>
                    <ResourceMeter usage={pod.cpu} quota={pod.cpu_limit} zh={zh} />
                  </td>
                  <td>
                    <ResourceMeter usage={pod.memory} quota={pod.memory_limit} zh={zh} />
                  </td>
                  <td className="mono text-xs">{pod.restarts}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function InstanceDetailPanel({
  instance,
  componentStatus,
  componentStatusLoading,
  componentStatusError,
  onSaveConnectivity,
  savingConnectivity,
  onRefresh,
  probing,
  checkResult,
}: Props) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const d = instance.data;
  const components = componentStatus?.components || [];
  const [editingField, setEditingField] = useState<HostField | null>(null);
  const [draftHost, setDraftHost] = useState('');

  const beginEdit = (field: HostField, value: string) => {
    setEditingField(field);
    setDraftHost(value);
  };

  const cancelEdit = () => {
    setEditingField(null);
    setDraftHost('');
  };

  const saveEdit = async () => {
    if (!editingField) return;
    const ok = await onSaveConnectivity({ [editingField]: draftHost.trim() });
    if (ok) cancelEdit();
  };

  const endpoints: { field: HostField; label: string; value: string; placeholder: string }[] = [
    {
      field: 'gateway_host',
      label: 'Gateway',
      value: d?.gateway_host || '',
      placeholder: 'http://jiuwenswarm-gateway:19000',
    },
    {
      field: 'runtime_host',
      label: 'Runtime',
      value: d?.runtime_host || '',
      placeholder: 'http://jiuwenswarm-agent-runtime:8091',
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header flex-wrap items-start gap-y-3">
        <ClusterContentHeading section="status" />
        <div className="flex flex-wrap gap-2">
          <button className="btn sm" disabled={probing} onClick={onRefresh}>
            {probing
              ? zh
                ? '刷新中…'
                : 'Refreshing…'
              : t('common.refresh')}
          </button>
        </div>
      </div>
      {checkResult && (
        <p className="text-sm text-muted" role="status">
          {checkResult}
        </p>
      )}

      <div className="card">
        <div className="card-header">
          <div className="card-title">{t('instanceDetail.connectivity')}</div>
        </div>
        <div className="connectivity-card__grid">
          {endpoints.map((item) => {
            const editing = editingField === item.field;
            const empty = !item.value.trim();
            return (
              <div
                key={item.field}
                className={`connectivity-endpoint${editing ? ' is-editing' : ''}`}
              >
                <div className="connectivity-endpoint__top">
                  <span className="connectivity-endpoint__label">{item.label}</span>
                  <div className="connectivity-endpoint__actions">
                    {editing ? (
                      <>
                        <button
                          type="button"
                          className="connectivity-endpoint__action"
                          disabled={savingConnectivity}
                          onClick={cancelEdit}
                        >
                          {t('common.cancel')}
                        </button>
                        <button
                          type="button"
                          className="connectivity-endpoint__action is-primary"
                          disabled={savingConnectivity}
                          onClick={() => void saveEdit()}
                        >
                          {savingConnectivity
                            ? zh
                              ? '保存中…'
                              : 'Saving…'
                            : t('common.save')}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="connectivity-endpoint__action is-primary"
                        disabled={editingField != null && !editing}
                        onClick={() => beginEdit(item.field, item.value)}
                      >
                        {zh ? '编辑' : 'Edit'}
                      </button>
                    )}
                  </div>
                </div>
                {editing ? (
                  <LimitedTextInput
                    className="connectivity-endpoint__input"
                    value={draftHost}
                    maxLength={HOST_MAX}
                    placeholder={item.placeholder}
                    autoFocus
                    onChange={setDraftHost}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        void saveEdit();
                      } else if (e.key === 'Escape') {
                        e.preventDefault();
                        cancelEdit();
                      }
                    }}
                  />
                ) : (
                  <p
                    className={`connectivity-endpoint__value${empty ? ' is-empty' : ''}`}
                    title={item.value || undefined}
                  >
                    {empty ? '—' : item.value}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {componentStatusError && (
        <div className="card text-danger" role="alert">
          {zh ? `组件状态读取失败：${componentStatusError}` : `Failed to load component status: ${componentStatusError}`}
        </div>
      )}
      {componentStatus?.k8s_error && !componentStatusError && (
        <div className="card text-muted text-sm" role="status">
          {zh ? `K8s 库存降级：${componentStatus.k8s_error}` : `K8s inventory degraded: ${componentStatus.k8s_error}`}
        </div>
      )}

      {componentStatusLoading && !components.length ? (
        <div className="card" role="status">
          {zh ? '正在读取组件与 Pod…' : 'Loading components and pods…'}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {components.map((block) => (
            <ComponentCard key={block.type} block={block} zh={zh} />
          ))}
        </div>
      )}
    </div>
  );
}
