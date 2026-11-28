import { ApiError } from '../services/api';

type Translate = (key: string, options?: Record<string, unknown>) => string;

type ValidationDetail = {
  code?: string;
  message?: string;
  name?: string;
  names?: string[] | string;
};

function asNames(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(String).filter(Boolean).join(', ');
  }
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return '';
}

function detailObject(error: ApiError): ValidationDetail | undefined {
  const raw = error.raw as { detail?: unknown } | undefined;
  const detail = raw?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return detail as ValidationDetail;
  }
  return undefined;
}

const CODE_TO_KEY: Record<string, string> = {
  duplicate_env_name: 'templateValidation.duplicateEnvName',
  volumes_not_mounted: 'templateValidation.volumesNotMounted',
  volume_mount_undefined: 'templateValidation.volumeMountUndefined',
};

/** 运行时/容器模板校验错误的本地化提示；无法识别时返回 undefined。 */
export function templateValidationMessage(
  error: unknown,
  t: Translate,
): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const detail = detailObject(error);
  const code = detail?.code;
  if (!code) return undefined;
  const key = CODE_TO_KEY[code];
  if (!key) return undefined;
  const name = typeof detail?.name === 'string' ? detail.name : '';
  const names = asNames(detail?.names) || name;
  return t(key, { name: name || names, names: names || name });
}
