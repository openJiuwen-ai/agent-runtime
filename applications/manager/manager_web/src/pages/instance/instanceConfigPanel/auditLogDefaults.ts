/**
 * 与 foundation/audit/constants.py 字段清单对齐（本阶段封闭集）。
 * otel 不再由 Manager 前端配置（部署 env / telemetry）；库表列可保留。
 */
import type {
  AuditLogConfig,
  AuditLogConfigUpsertBody,
  AuditLogFormatSpec,
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
  format: AuditLogFormatSpec;
}

export const DEFAULT_FORM: AuditLogFormState = {
  data_center: 'N',
  system_code: '-',
  node: '',
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

export function mapFromGet(data: AuditLogConfig): AuditLogFormState {
  const body = (data.body && typeof data.body === 'object' ? data.body : {}) as NonNullable<
    AuditLogConfig['body']
  >;
  const format = (body.format && typeof body.format === 'object'
    ? body.format
    : {}) as Partial<AuditLogFormatSpec>;

  return {
    data_center: String(body.data_center ?? data.data_center ?? DEFAULT_FORM.data_center),
    system_code: String(body.system_code ?? data.system_code ?? DEFAULT_FORM.system_code),
    node: String(body.node ?? data.node ?? ''),
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

/** 仅提交 format + identity；otel 由部署侧配置，不经 Manager 下发。 */
export function toUpsertBody(form: AuditLogFormState): AuditLogConfigUpsertBody {
  return {
    format: {
      ...form.format,
      timestamp_format: TIMESTAMP_FORMAT_FIXED,
    },
    data_center: form.data_center.trim() || undefined,
    system_code: form.system_code.trim() || undefined,
    node: form.node.trim() || undefined,
  };
}

export type AuditLogValidateErrorKey =
  | 'fieldsEmpty'
  | 'requiredNotSubset'
  | 'placeholderRequired'
  | 'schemaVersionRequired';

export function clientValidate(form: AuditLogFormState): AuditLogValidateErrorKey | null {
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
  return null;
}

/** D6：必填勾选时，若尚未启用则自动加入对应分组 */
export function ensureEnabledForRequired(
  form: AuditLogFormState,
  field: string
): AuditLogFormState {
  if (HEADER_SET.has(field) && !form.format.header_fields.includes(field)) {
    return {
      ...form,
      format: {
        ...form.format,
        header_fields: [...form.format.header_fields, field],
      },
    };
  }
  if (CONTENT_SET.has(field) && !form.format.content_fields.includes(field)) {
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
    format: {
      ...form.format,
      header_fields: [...DEFAULT_HEADER_FIELDS],
      content_fields: [...DEFAULT_CONTENT_FIELDS],
      required_fields: [...DEFAULT_REQUIRED_FIELDS],
      schema_version: '1.0.0',
      placeholder: '-',
      timestamp_format: TIMESTAMP_FORMAT_FIXED,
    },
  };
}
