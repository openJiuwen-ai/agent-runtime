import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  PrometheusApi,
  PrometheusQueryResponse,
} from '../../services/api';

interface PodUsage {
  namespace: string;
  pod: string;
  /** CPU 用量（核），近 5 分钟均值 */
  cpuCores: number;
  /** 内存工作集（字节） */
  memoryBytes: number;
}

// 容器级指标聚合到 Pod；排除 pause/空容器
const CPU_QUERY =
  'sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m]))';
const MEM_QUERY =
  'sum by (namespace, pod) (container_memory_working_set_bytes{container!="",container!="POD"})';

const REFRESH_INTERVAL_MS = 10_000;

function toValueMap(resp: PrometheusQueryResponse): Map<string, number> {
  const map = new Map<string, number>();
  for (const item of resp.data?.result ?? []) {
    const { namespace, pod } = item.metric;
    if (!namespace || !pod) continue;
    map.set(`${namespace}/${pod}`, Number(item.value?.[1] ?? 0));
  }
  return map;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} Gi`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} Mi`;
  return `${(bytes / 1024).toFixed(0)} Ki`;
}

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function MonitoringTab() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<PodUsage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [namespace, setNamespace] = useState('');
  const [search, setSearch] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cpu, mem] = await Promise.all([
        PrometheusApi.query(CPU_QUERY),
        PrometheusApi.query(MEM_QUERY),
      ]);
      const cpuMap = toValueMap(cpu);
      const memMap = toValueMap(mem);
      const merged: PodUsage[] = [];
      for (const key of new Set([...cpuMap.keys(), ...memMap.keys()])) {
        const idx = key.indexOf('/');
        const namespace = key.slice(0, idx);
        const pod = key.slice(idx + 1);
        merged.push({
          namespace,
          pod,
          cpuCores: cpuMap.get(key) ?? 0,
          memoryBytes: memMap.get(key) ?? 0,
        });
      }
      merged.sort(
        (a, b) =>
          a.namespace.localeCompare(b.namespace) || a.pod.localeCompare(b.pod),
      );
      setRows(merged);
      setUpdatedAt(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [autoRefresh, load]);

  const namespaces = useMemo(
    () => [...new Set(rows.map((r) => r.namespace))].sort(),
    [rows],
  );

  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (!namespace || r.namespace === namespace) &&
          (!search ||
            r.pod.toLowerCase().includes(search.toLowerCase())),
      ),
    [rows, namespace, search],
  );

  const totalCpu = filtered.reduce((acc, r) => acc + r.cpuCores, 0);
  const totalMem = filtered.reduce((acc, r) => acc + r.memoryBytes, 0);

  return (
    <div className="space-y-4">
      <div className="card p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold">{t('observability.monitoring.filterTitle')}</div>
          <div className="flex items-center gap-2 text-xs text-muted">
            {updatedAt && (
              <span>
                {t('observability.monitoring.updatedAt', { time: formatTime(updatedAt.getTime()) })}
              </span>
            )}
            <label className="flex items-center gap-1 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              {t('observability.monitoring.autoRefresh')}
            </label>
            <button type="button" className="input" style={{ width: 'auto' }} onClick={load}>
              {t('common.refresh')}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="input"
            style={{ width: '200px' }}
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
          >
            <option value="">{t('observability.monitoring.allNamespaces')}</option>
            {namespaces.map((ns) => (
              <option key={ns} value={ns}>
                {ns}
              </option>
            ))}
          </select>
          <input
            className="input"
            style={{ width: '200px' }}
            placeholder={t('observability.monitoring.searchPod')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <span className="text-xs text-muted">
            {t('observability.monitoring.summary', {
              pods: filtered.length,
              cpu: totalCpu.toFixed(2),
              mem: formatBytes(totalMem),
            })}
          </span>
        </div>
      </div>

      {error && <div className="card p-4 text-danger">{error}</div>}

      <div className="card p-0 overflow-hidden">
        {loading && rows.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.loading')}</div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.empty')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted">
                <th className="px-3 py-2">{t('observability.monitoring.col.namespace')}</th>
                <th className="px-3 py-2">{t('observability.monitoring.col.pod')}</th>
                <th className="px-3 py-2 text-right">{t('observability.monitoring.col.cpu')}</th>
                <th className="px-3 py-2 text-right">{t('observability.monitoring.col.memory')}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={`${r.namespace}/${r.pod}`} className="border-b hover:bg-muted/50">
                  <td className="px-3 py-2 whitespace-nowrap text-muted text-xs">{r.namespace}</td>
                  <td className="px-3 py-2 font-medium truncate max-w-md" title={r.pod}>
                    {r.pod}
                  </td>
                  <td className="px-3 py-2 text-right num whitespace-nowrap">
                    {r.cpuCores.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-right num whitespace-nowrap">
                    {formatBytes(r.memoryBytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
