import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { canAccessManager, isPlatformAdmin } from '../services/api';

function initialsOf(name: string | null | undefined, fallback: string): string {
  const text = (name || fallback || '?').trim();
  if (!text) return '?';
  const parts = text.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0]![0] ?? ''}${parts[1]![0] ?? ''}`.toUpperCase();
  }
  return text.slice(0, 2).toUpperCase();
}

/** 顶栏圆形身份入口：弹出卡片可进入用户面 / 退出登录。 */
export function UserMenu() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const roleLabel = useMemo(() => {
    if (!user) return '';
    if (isPlatformAdmin(user)) return t('iam.roleAdmin');
    if (user.manager_access) return t('iam.roleManager');
    return t('iam.roleUser');
  }, [t, user]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (!user) return null;

  const initials = initialsOf(user.display_name, user.user_id);

  return (
    <div className="user-menu relative" ref={rootRef}>
      <button
        type="button"
        className={`user-menu__avatar ${open ? 'is-open' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={user.display_name || user.user_id}
        title={user.display_name || user.user_id}
        onClick={() => setOpen((current) => !current)}
      >
        {initials}
      </button>

      {open && (
        <div className="user-menu__card dropdown-menu-surface" role="menu">
          <div className="user-menu__identity">
            <div className="user-menu__name truncate" title={user.display_name || user.user_id}>
              {user.display_name || user.user_id}
            </div>
            <div className="user-menu__role">{roleLabel}</div>
          </div>
          <div className="user-menu__actions">
            {canAccessManager(user) && (
              <button
                type="button"
                className="dropdown-menu-option user-menu__action"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  window.location.href = '/user';
                }}
              >
                {t('auth.switchToUser')}
              </button>
            )}
            <button
              type="button"
              className="dropdown-menu-option user-menu__action is-danger"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void logout();
              }}
            >
              {t('auth.logout')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
