import { ApiError } from '../services/api';

type Translate = (key: string, options?: Record<string, unknown>) => string;

type InUseAction = 'disable' | 'delete';

/** 后端模板引用错误码 → i18n key（与操作无关的通用提示）。 */
const CODE_TO_KEY: Record<string, string> = {
  agent_definitions_referenced: 'templateInUse.agentDefinitions',
  agent_config_referenced_by_instances: 'templateInUse.agentConfigInstances',
  runtime_referenced_by_services: 'templateInUse.runtimeServices',
};

function agentTemplatesKey(action: InUseAction): string {
  return action === 'disable'
    ? 'templateInUse.agentTemplatesDisable'
    : 'templateInUse.agentTemplatesDelete';
}

/** 禁用前检查：仍被 Agent 配置模板引用则返回本地化提示。 */
export function disableBlockedByReferences(
  enabled: boolean,
  referenceCount: number | undefined,
  t: Translate,
): string | undefined {
  if (enabled || (referenceCount ?? 0) <= 0) return undefined;
  return t(agentTemplatesKey('disable'), { count: referenceCount ?? 0 });
}

/** 删除前检查：仍被引用则直接提示，不再弹出确认框。 */
export function deleteBlockedByReferences(
  referenceCount: number | undefined,
  t: Translate,
): string | undefined {
  if ((referenceCount ?? 0) <= 0) return undefined;
  return t(agentTemplatesKey('delete'), { count: referenceCount ?? 0 });
}

/** 模板被引用时的本地化提示；不是该类错误时返回 `undefined`。 */
export function templateInUseMessage(
  error: unknown,
  t: Translate,
  action: InUseAction = 'delete',
): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const detail = (error.raw as { detail?: { code?: string; count?: number } } | undefined)
    ?.detail;
  const code = detail?.code;
  if (!code) return undefined;
  const count = detail?.count ?? 1;
  if (code === 'agent_templates_referenced') {
    return t(agentTemplatesKey(action), { count });
  }
  const key = CODE_TO_KEY[code];
  if (!key) return undefined;
  return t(key, { count });
}
