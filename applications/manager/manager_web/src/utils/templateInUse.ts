import { ApiError } from '../services/api';

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** 后端模板引用错误码 → i18n key。 */
const CODE_TO_KEY: Record<string, string> = {
  agent_definitions_referenced: 'templateInUse.agentDefinitions',
  agent_config_referenced_by_instances: 'templateInUse.agentConfigInstances',
  runtime_referenced_by_services: 'templateInUse.runtimeServices',
  agent_templates_referenced: 'templateInUse.agentTemplates',
};

/** 禁用前检查：仍被 Agent 配置模板引用则返回本地化提示。 */
export function disableBlockedByReferences(
  enabled: boolean,
  referenceCount: number | undefined,
  t: Translate,
): string | undefined {
  if (enabled || (referenceCount ?? 0) <= 0) return undefined;
  return t('templateInUse.agentTemplates', { count: referenceCount ?? 0 });
}

/** 模板被引用时的本地化提示；不是该类错误时返回 `undefined`。 */
export function templateInUseMessage(
  error: unknown,
  t: Translate,
): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const detail = (error.raw as { detail?: { code?: string; count?: number } } | undefined)
    ?.detail;
  const key = detail?.code ? CODE_TO_KEY[detail.code] : undefined;
  if (!key) return undefined;
  return t(key, { count: detail?.count ?? 1 });
}
