import { tryParseJson } from '../../../components/JsonField';
import { safeStringify } from '../../../utils/format';
import { stripExampleLabel } from '../../../utils/jsonExample';
import type {
  PermissionAction,
  PermissionMode,
  PermissionRuleEntry,
  PermissionSeverity,
  PermissionToolEntry,
  PermissionsFormState,
} from '../../../types';

export { stripExampleLabel } from '../../../utils/jsonExample';

const EMPTY_FILE_GUARD_JSON = '{}';

const DEFAULT_FILE_GUARD = {
  enabled: true,
  defaults: { read: 'ask', write: 'ask', exec: 'ask' },
  workspace: { read: 'allow', write: 'allow', exec: 'allow' },
  paths: [] as unknown[],
};

let _rowKey = 0;
function nextKey(prefix: string) {
  _rowKey += 1;
  return `${prefix}-${_rowKey}`;
}

function asPermissionAction(value: unknown, fallback: PermissionAction = 'ask'): PermissionAction {
  if (value === 'allow' || value === 'ask' || value === 'deny') return value;
  return fallback;
}

function asPermissionMode(value: unknown): PermissionMode {
  return value === 'strict' ? 'strict' : 'normal';
}

function asSeverity(value: unknown, fallback: PermissionSeverity = 'LOW'): PermissionSeverity {
  if (value === 'LOW' || value === 'MEDIUM' || value === 'HIGH' || value === 'CRITICAL') return value;
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function parseJsonField<T>(text: string, fallback: T): T {
  return tryParseJson(stripExampleLabel(text), fallback);
}

function defaultsFromBody(value: unknown): Record<string, PermissionAction> {
  if (typeof value === 'string') {
    return { '*': asPermissionAction(value, 'allow') };
  }
  const record = asRecord(value);
  const result: Record<string, PermissionAction> = {};
  for (const [key, action] of Object.entries(record)) {
    result[key] = asPermissionAction(action, 'allow');
  }
  if (Object.keys(result).length === 0) {
    return { '*': 'allow' };
  }
  return result;
}

function toolsListFromBody(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === 'string' && value.trim()) {
    return value.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean);
  }
  return [];
}

/** 兼容旧版细分 file_guard 字段，合并为整块对象 */
function fileGuardObjectFromBody(body: Record<string, unknown>): Record<string, unknown> {
  const fileGuard = asRecord(body.file_guard);
  if (Object.keys(fileGuard).length > 0) {
    // 已是新结构（enabled / defaults / workspace / paths）或任意完整对象
    if (
      'enabled' in fileGuard ||
      'paths' in fileGuard ||
      'defaults' in fileGuard ||
      (!('global' in fileGuard) && !('tool_bindings' in fileGuard) && !('trusted_exec_directory' in fileGuard))
    ) {
      return fileGuard;
    }
    // 旧结构：workspace.rw_enabled + global / trusted_exec / tool_bindings
    const workspace = asRecord(fileGuard.workspace);
    return {
      enabled: fileGuard.enabled !== false,
      defaults: fileGuard.defaults ?? { read: 'ask', write: 'ask', exec: 'ask' },
      workspace: {
        read: workspace.rw_enabled === false ? 'ask' : (workspace.read ?? 'allow'),
        write: workspace.rw_enabled === false ? 'ask' : (workspace.write ?? 'allow'),
        exec: workspace.exec ?? 'allow',
      },
      paths: Array.isArray(fileGuard.paths) ? fileGuard.paths : [],
      ...(Object.keys(asRecord(fileGuard.global)).length > 0 ? { global: fileGuard.global } : {}),
      ...(Array.isArray(fileGuard.trusted_exec_directory) && fileGuard.trusted_exec_directory.length > 0
        ? { trusted_exec_directory: fileGuard.trusted_exec_directory }
        : {}),
      ...(Object.keys(asRecord(fileGuard.tool_bindings)).length > 0
        ? { tool_bindings: fileGuard.tool_bindings }
        : {}),
    };
  }
  return { ...DEFAULT_FILE_GUARD };
}

function fileGuardJsonFromBody(body: Record<string, unknown>): string {
  const obj = fileGuardObjectFromBody(body);
  if (Object.keys(obj).length === 0) return EMPTY_FILE_GUARD_JSON;
  return safeStringify(obj, 2);
}

export function createPermissionToolEntry(name: string, action: PermissionAction): PermissionToolEntry {
  return { key: nextKey('tool'), name: name.trim(), action };
}

export function createDefaultPermissionsFormState(): PermissionsFormState {
  return {
    enabled: true,
    schema: 'tiered_policy',
    permissionMode: 'normal',
    defaults: { '*': 'allow' },
    tools: [],
    rules: [],
    ownerScopes: {},
    denyGuidanceMessage: '',
    externalDirectory: undefined,
    fileGuardJson: safeStringify(DEFAULT_FILE_GUARD, 2),
  };
}

export function permissionsBodyToFormState(body: Record<string, unknown>): PermissionsFormState {
  const defaults = createDefaultPermissionsFormState();
  const toolsRaw = asRecord(body.tools);
  const tools: PermissionToolEntry[] = Object.entries(toolsRaw).map(([name, action]) => ({
    key: nextKey('tool'),
    name,
    action: asPermissionAction(action),
  }));

  const rulesRaw = Array.isArray(body.rules) ? body.rules : [];
  const rules: PermissionRuleEntry[] = rulesRaw
    .filter((item) => item && typeof item === 'object')
    .map((item) => {
      const row = item as Record<string, unknown>;
      return {
        key: nextKey('rule'),
        id: String(row.id ?? ''),
        tools: toolsListFromBody(row.tools),
        pattern: String(row.pattern ?? ''),
        severity: asSeverity(row.severity),
      };
    });

  return {
    enabled: body.enabled !== false,
    schema: typeof body.schema === 'string' && body.schema.trim() ? body.schema.trim() : defaults.schema,
    permissionMode: asPermissionMode(body.permission_mode),
    defaults: defaultsFromBody(body.defaults),
    tools,
    rules,
    ownerScopes: asRecord(body.owner_scopes),
    denyGuidanceMessage: String(body.deny_guidance_message ?? ''),
    externalDirectory: (() => {
      const record = asRecord(body.external_directory);
      return Object.keys(record).length > 0 ? record : undefined;
    })(),
    fileGuardJson: fileGuardJsonFromBody(body),
  };
}

export function permissionsFormStateToBody(form: PermissionsFormState): Record<string, unknown> {
  const tools: Record<string, PermissionAction> = {};
  for (const row of form.tools) {
    const name = row.name.trim();
    if (!name) continue;
    tools[name] = row.action;
  }

  const rules = form.rules
    .map((row) => {
      const id = row.id.trim();
      const pattern = row.pattern.trim();
      if (!id || !pattern) return null;
      const item: Record<string, unknown> = {
        id,
        tools: row.tools.map((t) => t.trim()).filter(Boolean),
        pattern,
        severity: row.severity,
      };
      return item;
    })
    .filter(Boolean) as Record<string, unknown>[];

  const defaults =
    Object.keys(form.defaults).length > 0 ? form.defaults : { '*': 'allow' as PermissionAction };

  const body: Record<string, unknown> = {
    enabled: form.enabled,
    schema: form.schema || 'tiered_policy',
    permission_mode: form.permissionMode,
    defaults,
    tools,
    rules,
    owner_scopes: form.ownerScopes,
    deny_guidance_message: form.denyGuidanceMessage,
    file_guard: parseJsonField(form.fileGuardJson, { ...DEFAULT_FILE_GUARD }),
  };

  if (form.externalDirectory !== undefined) {
    body.external_directory = form.externalDirectory;
  }

  return body;
}
