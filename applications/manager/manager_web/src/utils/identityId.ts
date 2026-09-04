/** user_id / group_id：仅英文字母、数字、下划线、连字符。 */

export const IDENTITY_ID_MAX_LENGTH = 64;
export const IDENTITY_ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export function sanitizeIdentityIdInput(raw: string): string {
  return raw.replace(/[^A-Za-z0-9_-]/g, '').slice(0, IDENTITY_ID_MAX_LENGTH);
}

export function isValidIdentityId(value: string): boolean {
  const trimmed = value.trim();
  return (
    trimmed.length > 0 &&
    trimmed.length <= IDENTITY_ID_MAX_LENGTH &&
    IDENTITY_ID_PATTERN.test(trimmed)
  );
}
