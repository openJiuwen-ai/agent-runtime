export interface LogMaskingRule {
  id: number;
  jiuwenclaw_id: string;
  rule_id: string;
  rule_name: string;
  description?: string | null;
  pattern: string;
  replacement: string;
  priority: number;
  with_fingerprint: boolean;
  source: string;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LogMaskingRuleCreateBody {
  rule_name: string;
  description?: string;
  pattern: string;
  replacement?: string;
  priority?: number;
  with_fingerprint?: boolean;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type LogMaskingRuleUpdateBody = Partial<LogMaskingRuleCreateBody>;

export type PermissionAction = 'allow' | 'ask' | 'deny';
export type PermissionMode = 'normal' | 'strict';
export type PermissionSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface PermissionToolEntry {
  key: string;
  name: string;
  action: PermissionAction;
}

/** 与 config.yaml permissions.rules[*] 对齐 */
export interface PermissionRuleEntry {
  key: string;
  id: string;
  tools: string[];
  pattern: string;
  severity: PermissionSeverity;
}

export interface PermissionsFormState {
  enabled: boolean;
  /** 技能加载时是否根据 SKILL.md 权限声明触发动态授权 */
  skillAuthorizationEnabled: boolean;
  schema: string;
  permissionMode: PermissionMode;
  /** config.yaml defaults，如 { '*': 'allow' } */
  defaults: Record<string, PermissionAction>;
  tools: PermissionToolEntry[];
  rules: PermissionRuleEntry[];
  ownerScopes: Record<string, unknown>;
  denyGuidanceMessage: string;
  externalDirectory?: Record<string, unknown>;
  /** 整块 file_guard JSON，与 config.yaml 字段一致 */
  fileGuardJson: string;
}

export interface ListItemsResult<T> {
  items: T[];
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | 'NOTSET';

export interface LoggingConfig {
  id?: number;
  jiuwenclaw_id: string;
  level: LogLevel;
  console_level?: LogLevel | null;
  gateway?: LogLevel | null;
  channel?: LogLevel | null;
  agent_server?: LogLevel | null;
  full?: LogLevel | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LoggingConfigUpsertBody {
  level: LogLevel;
  console_level?: LogLevel | null;
  gateway?: LogLevel | null;
  channel?: LogLevel | null;
  agent_server?: LogLevel | null;
  full?: LogLevel | null;
}

export type AuditOtelProtocol = 'grpc' | 'http';

export interface AuditLogFormatSpec {
  schema_version: string;
  header_fields: string[];
  content_fields: string[];
  required_fields: string[];
  placeholder: string;
  timestamp_format: string;
}

export interface AuditLogOtelConfig {
  enabled: boolean;
  endpoint: string;
  protocol: AuditOtelProtocol;
  headers: Record<string, string>;
}

/** PUT body（与 AuditLogUpsertRequest 对齐；不含 service / ntp） */
export interface AuditLogConfigUpsertBody {
  format: AuditLogFormatSpec;
  otel?: AuditLogOtelConfig;
  data_center?: string | null;
  system_code?: string | null;
  node?: string | null;
}

/** GET data（扁平列 + 权威 body） */
export interface AuditLogConfig {
  id?: number;
  jiuwenclaw_id: string;
  schema_version?: string;
  otel_enabled?: boolean;
  otel_endpoint?: string | null;
  otel_protocol?: string;
  data_center?: string;
  system_code?: string;
  node?: string | null;
  body?: {
    format?: AuditLogFormatSpec;
    otel?: Partial<AuditLogOtelConfig> & { headers?: Record<string, string> };
    data_center?: string;
    system_code?: string;
    node?: string | null;
  } | null;
  source?: string;
  revision?: number;
  created_at?: string | null;
  updated_at?: string | null;
}
