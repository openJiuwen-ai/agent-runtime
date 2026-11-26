import i18n from '../i18n';

type Translate = (key: string, options?: Record<string, unknown>) => string;

type RoleLike = {
  role_id: string;
  name?: string | null;
  description?: string | null;
  is_system?: boolean;
};

type PermissionLike = {
  permission_id: string;
  name?: string | null;
  description?: string | null;
};

/** Map `deployment:read` → `roles.permissionTexts.deployment.read` (avoid i18next `:` nsSeparator). */
function permissionKeyBase(permissionId: string): string {
  return `roles.permissionTexts.${permissionId.split(':').filter(Boolean).join('.')}`;
}

/** System / seeded role display name; custom roles keep API text. */
export function displayRoleName(role: RoleLike, t: Translate): string {
  const key = `roles.systemRoles.${role.role_id}.name`;
  if ((role.is_system || role.role_id === 'platform_admin') && i18n.exists(key)) {
    return t(key);
  }
  return role.name?.trim() || role.role_id;
}

/** System / seeded role description; custom roles keep API text. */
export function displayRoleDescription(role: RoleLike, t: Translate): string {
  const key = `roles.systemRoles.${role.role_id}.description`;
  if ((role.is_system || role.role_id === 'platform_admin') && i18n.exists(key)) {
    return t(key);
  }
  return role.description?.trim() || '';
}

/** Seeded permission display name; unknown ids fall back to API. */
export function displayPermissionName(permission: PermissionLike, t: Translate): string {
  const key = `${permissionKeyBase(permission.permission_id)}.name`;
  if (i18n.exists(key)) return t(key);
  return permission.name?.trim() || permission.permission_id;
}

/** Seeded permission description; unknown ids fall back to API. */
export function displayPermissionDescription(
  permission: PermissionLike,
  t: Translate,
): string {
  const key = `${permissionKeyBase(permission.permission_id)}.description`;
  if (i18n.exists(key)) return t(key);
  return permission.description?.trim() || '';
}
