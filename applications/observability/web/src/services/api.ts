export class ApiError extends Error {
  constructor(public status: number, public detail: string, public raw?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
  }
}

function buildQuery(query?: Record<string, string | number | boolean | null | undefined>): string {
  if (!query) return '';
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

// ---------------------------------------------------------------------------
// Loki
// ---------------------------------------------------------------------------

export interface LokiStreamValue {
  stream: Record<string, string>;
  values: [string, string][];  // [timestamp_ns, log_line]
}
export interface LokiQueryRangeResponse {
  status: string;
  data: {
    resultType: string;
    result: LokiStreamValue[];
  };
}

async function httpLoki<T>(
  path: string,
  query?: Record<string, string>,
): Promise<T> {
  const url = `/loki${path}${buildQuery(
    (query ?? {}) as Record<string, string | number | boolean | null | undefined>,
  )}`;
  let resp: Response;
  try {
    resp = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }
  const text = await resp.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      // non-JSON
    }
  }
  if (!resp.ok) {
    const detail =
      (json && typeof json === 'object' && 'error' in (json as Record<string, unknown>)
        ? String((json as { error: unknown }).error)
        : '') || resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  return json as T;
}

/** 设计文档 §4 / §6 默认 header_fields */
export const DESIGN_HEADER_FIELDS = [
  'schema_version',
  'timestamp',
  'level',
  'data_center',
  'system_code',
  'node',
  'trace_id',
  'txn_seq',
  'pid',
  'tid',
  'caller',
] as const;

/** 设计文档 §4 / §6 默认 content_fields */
export const DESIGN_CONTENT_FIELDS = [
  'UID',
  'CUSTID',
  'SRCIP',
  'DSTIP',
  'COST',
  'UA',
  'EVT',
  'MSG',
  'RSPCD',
  'SUBMDL',
  'PROC',
  'SVRNAM',
  'ACTION',
  'SANDBOXID',
  'RESULT',
] as const;

/** 固定写入 attributes、但不在 header/content 清单中的键 */
export const DESIGN_FIXED_FIELDS = ['event_type'] as const;

/** 展开「扩展」时默认隐藏的 OTel / Loki 噪声键 */
export const NOISE_ATTRIBUTE_KEYS = new Set([
  'telemetry_sdk_language',
  'telemetry_sdk_name',
  'telemetry_sdk_version',
  'scope_name',
  'severity_number',
  'severity_text',
  'detected_level',
  'flags',
  'span_id',
  'service_version',
  'service_name',
]);

export interface AuditLogEntry {
  timestamp: number; // ms（列表排序用 Loki ns）
  eventType: string;
  serviceName: string;
  submdl: string;
  proc: string;
  result: string;
  error: string;
  traceId: string;
  txnSeq: string;
  userId: string;
  agentPod: string;
  body: string;
  /** Loki stream 全量 labels（含设计字段） */
  attributes: Record<string, string>;
  raw: string;
}

function pickAttr(labels: Record<string, string>, key: string): string {
  const v = labels[key];
  if (v !== undefined && v !== null && String(v).trim() !== '') {
    return String(v);
  }
  return '';
}

export function parseLokiAuditStreams(resp: LokiQueryRangeResponse): AuditLogEntry[] {
  const entries: AuditLogEntry[] = [];
  for (const stream of resp.data?.result ?? []) {
    const labels = { ...(stream.stream ?? {}) };
    for (const [tsNs, line] of stream.values) {
      const ts = Math.floor(Number(tsNs) / 1e6);
      entries.push({
        timestamp: ts,
        eventType: pickAttr(labels, 'event_type'),
        serviceName: pickAttr(labels, 'service_name').replace(/^jiuwenclaw-/, ''),
        submdl: pickAttr(labels, 'SUBMDL'),
        proc: pickAttr(labels, 'PROC'),
        result: pickAttr(labels, 'RESULT'),
        error: pickAttr(labels, 'MSG'),
        traceId: pickAttr(labels, 'trace_id'),
        txnSeq: pickAttr(labels, 'txn_seq'),
        userId: pickAttr(labels, 'UID'),
        agentPod: pickAttr(labels, 'agent_pod'),
        body: line,
        attributes: labels,
        raw: line,
      });
    }
  }
  entries.sort((a, b) => b.timestamp - a.timestamp);
  return entries;
}

/** 按设计顺序取出字段；缺失时用 '-' */
export function orderedDesignFields(
  attributes: Record<string, string>,
  keys: readonly string[],
): { key: string; value: string }[] {
  return keys.map((key) => ({
    key,
    value: attributes[key] !== undefined && attributes[key] !== '' ? attributes[key] : '-',
  }));
}

/** 扩展区：不在设计清单中的其余键 */
export function extensionFields(
  attributes: Record<string, string>,
  showNoise = false,
): { key: string; value: string }[] {
  const known = new Set<string>([
    ...DESIGN_HEADER_FIELDS,
    ...DESIGN_CONTENT_FIELDS,
    ...DESIGN_FIXED_FIELDS,
  ]);
  return Object.keys(attributes)
    .filter((k) => !known.has(k))
    .filter((k) => showNoise || !NOISE_ATTRIBUTE_KEYS.has(k))
    .sort()
    .map((key) => ({ key, value: attributes[key] ?? '-' }));
}

export const LokiApi = {
  queryRange: (query: string, start: number, end: number, limit = 500) =>
    httpLoki<LokiQueryRangeResponse>('/api/v1/query_range', {
      query,
      start: String(start),
      end: String(end),
      limit: String(limit),
      direction: 'backward',
    }),
};
