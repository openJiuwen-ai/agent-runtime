/**
 * 与 foundation/audit/constants.py 字段清单对齐（本阶段封闭集）。
 */
import type {
  AuditLogConfig,
  AuditLogConfigUpsertBody,
  AuditLogFormatSpec,
  AuditLogOtelConfig,
  AuditOtelProtocol,
} from '../../../types';

export const DEFAULT_HEADER_FIELDS = [
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

export const DEFAULT_CONTENT_FIELDS = [
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

export const DEFAULT_REQUIRED_FIELDS = [
  'timestamp',
  'level',
  'UID',
  'CUSTID',
  'SRCIP',
  'DSTIP',
  'RSPCD',
  'SUBMDL',
  'PROC',
] as const;

export const TIMESTAMP_FORMAT_FIXED = 'yyyyMMdd-HH:mm:ss.SSS';

export const HEADER_FIELD_CANDIDATES = [...DEFAULT_HEADER_FIELDS];
export const CONTENT_FIELD_CANDIDATES = [...DEFAULT_CONTENT_FIELDS];

const HEADER_SET = new Set<string>(DEFAULT_HEADER_FIELDS);
const CONTENT_SET = new Set<string>(DEFAULT_CONTENT_FIELDS);

export interface AuditLogFormState {
  data_center: string;
  system_code: string;
  node: string;
  otel: AuditLogOtelConfig;
  format: AuditLogFormatSpec;
}

export const DEFAULT_FORM: AuditLogFormState = {
  data_center: 'N',
  system_code: '-',
  node: '',
  otel: {
    enabled: true,
    endpoint: 'http://localhost:4317',
    protocol: 'grpc',
    headers: {},
  },
  format: {
    schema_version: '1.0.0',
    header_fields: [...DEFAULT_HEADER_FIELDS],
    content_fields: [...DEFAULT_CONTENT_FIELDS],
    required_fields: [...DEFAULT_REQUIRED_FIELDS],
    placeholder: '-',
    timestamp_format: TIMESTAMP_FORMAT_FIXED,
  },
};

function asStringList(value: unknown, fallback: readonly string[]): string[] {
  if (!Array.isArray(value)) return [...fallback];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const name = String(item ?? '').trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out.length > 0 ? out : [...fallback];
}

function normalizeProtocol(value: unknown): AuditOtelProtocol {
  const p = String(value ?? 'grpc').trim().toLowerCase();
  return p === 'http' ? 'http' : 'grpc';
}

function normalizeHeaders(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = String(v ?? '');
  }
  return out;
}

export function mapFromGet(data: AuditLogConfig): AuditLogFormState {
  const body = (data.body && typeof data.body === 'object' ? data.body : {}) as NonNullable<
    AuditLogConfig['body']
  >;
  const format = (body.format && typeof body.format === 'object'
    ? body.format
    : {}) as Partial<AuditLogFormatSpec>;
  const otel = (body.otel && typeof body.otel === 'object'
    ? body.otel
    : {}) as Partial<AuditLogOtelConfig>;

  return {
    data_center: String(body.data_center ?? data.data_center ?? DEFAULT_FORM.data_center),
    system_code: String(body.system_code ?? data.system_code ?? DEFAULT_FORM.system_code),
    node: String(body.node ?? data.node ?? ''),
    otel: {
      enabled: Boolean(otel.enabled ?? data.otel_enabled ?? DEFAULT_FORM.otel.enabled),
      endpoint: String(
        otel.endpoint ?? data.otel_endpoint ?? DEFAULT_FORM.otel.endpoint
      ),
      protocol: normalizeProtocol(otel.protocol ?? data.otel_protocol),
      headers: normalizeHeaders(otel.headers),
    },
    format: {
      schema_version: String(format.schema_version ?? '1.0.0'),
      header_fields: asStringList(format.header_fields, DEFAULT_HEADER_FIELDS),
      content_fields: asStringList(format.content_fields, DEFAULT_CONTENT_FIELDS),
      required_fields: asStringList(format.required_fields, DEFAULT_REQUIRED_FIELDS),
      placeholder: String(format.placeholder ?? '-'),
      timestamp_format: TIMESTAMP_FORMAT_FIXED,
    },
  };
}

export function toUpsertBody(form: AuditLogFormState): AuditLogConfigUpsertBody {
  return {
    format: {
      ...form.format,
      timestamp_format: TIMESTAMP_FORMAT_FIXED,
    },
    otel: {
      enabled: form.otel.enabled,
      endpoint: form.otel.endpoint.trim(),
      protocol: form.otel.protocol,
      headers: form.otel.headers,
    },
    data_center: form.data_center.trim() || undefined,
    system_code: form.system_code.trim() || undefined,
    node: form.node.trim() || undefined,
  };
}

export type AuditLogValidateErrorKey =
  | 'endpointRequired'
  | 'headersInvalid'
  | 'fieldsEmpty'
  | 'requiredNotSubset'
  | 'placeholderRequired'
  | 'schemaVersionRequired';

export function clientValidate(
  form: AuditLogFormState,
  headersText: string
): AuditLogValidateErrorKey | null {
  if (!form.format.schema_version.trim()) return 'schemaVersionRequired';
  if (!form.format.placeholder.trim()) return 'placeholderRequired';
  if (
    form.format.header_fields.length === 0 ||
    form.format.content_fields.length === 0 ||
    form.format.required_fields.length === 0
  ) {
    return 'fieldsEmpty';
  }
  const enabled = new Set([...form.format.header_fields, ...form.format.content_fields]);
  if (form.format.required_fields.some((f) => !enabled.has(f))) {
    return 'requiredNotSubset';
  }
  if (form.otel.enabled && !form.otel.endpoint.trim()) {
    return 'endpointRequired';
  }
  try {
    const parsed = JSON.parse(headersText || '{}') as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return 'headersInvalid';
    }
  } catch {
    return 'headersInvalid';
  }
  return null;
}

export function parseHeadersText(headersText: string): Record<string, string> {
  const parsed = JSON.parse(headersText || '{}') as unknown;
  return normalizeHeaders(parsed);
}

export function headersToText(headers: Record<string, string>): string {
  return JSON.stringify(headers ?? {}, null, 2);
}

/** D6：必填勾选时，若尚未启用则自动加入对应分组 */
export function ensureEnabledForRequired(
  form: AuditLogFormState,
  field: string
): AuditLogFormState {
  const inHeader = form.format.header_fields.includes(field);
  const inContent = form.format.content_fields.includes(field);
  if (inHeader || inContent) return form;

  if (HEADER_SET.has(field)) {
    return {
      ...form,
      format: {
        ...form.format,
        header_fields: [...form.format.header_fields, field],
      },
    };
  }
  if (CONTENT_SET.has(field)) {
    return {
      ...form,
      format: {
        ...form.format,
        content_fields: [...form.format.content_fields, field],
      },
    };
  }
  return form;
}

export function restoreDefaultFields(form: AuditLogFormState): AuditLogFormState {
  return {
    ...form,
    format: { ...DEFAULT_FORM.format },
  };
}
