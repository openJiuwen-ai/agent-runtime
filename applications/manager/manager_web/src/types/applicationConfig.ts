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
