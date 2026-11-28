import { ApiError } from '../services/api';

type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * 把配额策略保存/删除时的后端错误转成已本地化的 toast 文案（issue #200）。
 *
 * - 5xx：上游 Gateway/Runtime 不可用，走通用中文兜底，不透传 raw detail，避免中英混合。
 * - 422：后端 ``_reject_detail`` 已把 Pydantic list 型 detail 逐条抽成可读文案
 *   （如 ``body.priority: Input should be a valid integer``），优先透传；
 *   detail 为空时回退到通用校验失败文案。
 * - 其他 4xx：保留可读业务 detail。
 *
 * 字段级中文提示由行内校验给出，toast 只负责操作结果兜底。
 */
export function quotaErrorMessage(
  e: unknown,
  t: Translate,
  mode: 'save' | 'delete' = 'save',
): string {
  if (e instanceof ApiError) {
    if (e.status >= 500) {
      return t(mode === 'delete' ? 'errors.upstreamDeleteFailed' : 'errors.upstreamSyncFailed');
    }
    if (e.status === 422) {
      const detail = e.detail?.trim();
      return detail || t('errors.validationFailed');
    }
    return e.detail || e.message;
  }
  return (e as Error).message;
}
