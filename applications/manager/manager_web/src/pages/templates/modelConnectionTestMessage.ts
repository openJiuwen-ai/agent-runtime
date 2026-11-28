import type { TFunction } from 'i18next';
import type { ModelTemplateConnectionTestResult } from '../../services/api';

const KNOWN_CODES = new Set([
  'timeout',
  'network',
  'auth',
  'balance',
  'rate_limit',
  'not_found',
  'model_not_found',
  'target_blocked',
  'redirect',
  'server',
  'http_error',
  'unknown',
]);

/** 把后端 error_code 映射成当前页面语言的可读 detail；未知时回退短原文。 */
export function connectionTestFailureDetail(
  result: Pick<ModelTemplateConnectionTestResult, 'error_code' | 'status_code' | 'detail'>,
  t: TFunction,
): string {
  const code = (result.error_code || '').trim();
  if (code && KNOWN_CODES.has(code)) {
    return t(`modelTemplate.testError.${code}`, {
      status: result.status_code ?? '',
    });
  }
  return (result.detail || '').trim();
}
