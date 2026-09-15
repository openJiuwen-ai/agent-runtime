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

export interface AuditLogEntry {
  timestamp: number;  // ms
  auditType: string;
  traceId: string;
  requestId: string;
  sessionId: string;
  userId: string;
  botId: string;
  groupId: string;
  agentName: string;
  agentPod: string;
  body: string;
  details: Record<string, string>;
  raw: string;
}

export function parseLokiAuditStreams(resp: LokiQueryRangeResponse): AuditLogEntry[] {
  const entries: AuditLogEntry[] = [];
  for (const stream of resp.data?.result ?? []) {
    const labels = stream.stream ?? {};
    for (const [tsNs, line] of stream.values) {
      const ts = Math.floor(Number(tsNs) / 1e6);
      entries.push({
        timestamp: ts,
        auditType: String(labels['audit_type'] ?? ''),
        traceId: String(labels['trace_id'] ?? ''),
        requestId: String(labels['request_id'] ?? ''),
        sessionId: String(labels['session_id'] ?? ''),
        userId: String(labels['user_id'] ?? ''),
        botId: String(labels['bot_id'] ?? ''),
        groupId: String(labels['group_id'] ?? ''),
        agentName: String(labels['agent_name'] ?? ''),
        agentPod: String(labels['agent_pod'] ?? ''),
        body: line,
        details: Object.entries(labels)
          .filter(([k]) => k.startsWith('audit_'))
          .reduce<Record<string, string>>((acc, [k, v]) => {
            acc[k.replace('audit_', '')] = String(v);
            return acc;
          }, {}),
        raw: line,
      });
    }
  }
  entries.sort((a, b) => b.timestamp - a.timestamp);
  return entries;
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
