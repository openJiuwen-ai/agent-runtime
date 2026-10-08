import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useRouter } from '../router';
import { ApprovalApi, hasPermission, isWorkspaceQuotaEnabled } from '../services/api';

const POLL_MS = 60_000;

function formatTodoCount(count: number): string {
  if (count > 99) return '99+';
  return String(count);
}

/** 顶栏「待办」入口：跳转审批中心，右上角显示待办数量角标。 */
export function ApprovalTodoButton() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { path, navigate } = useRouter();
  const [count, setCount] = useState(0);
  const canRead = hasPermission(user, 'approval:read') && isWorkspaceQuotaEnabled(user);

  const refresh = useCallback(async () => {
    if (!canRead) {
      setCount(0);
      return;
    }
    try {
      const result = await ApprovalApi.list({ view: 'todo', status: 'pending' });
      setCount(result.items?.length ?? 0);
    } catch {
      // 角标失败不影响主流程，保持上次数量
    }
  }, [canRead]);

  useEffect(() => {
    if (!canRead) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
    };
  }, [canRead, refresh]);

  useEffect(() => {
    if (!canRead) return;
    if (path === '/approvals' || path.startsWith('/approvals/')) {
      void refresh();
    }
  }, [canRead, path, refresh]);

  if (!canRead) return null;

  const active = path === '/approvals' || path.startsWith('/approvals/');
  const showBadge = count > 0;

  return (
    <button
      type="button"
      className={`approval-todo-btn btn ghost sm ${active ? 'is-active' : ''}`}
      onClick={() => navigate('/approvals')}
      aria-label={showBadge ? t('nav.todoWithCount', { count }) : t('nav.todo')}
      title={t('nav.todo')}
    >
      <span className="approval-todo-btn__label">{t('nav.todo')}</span>
      {showBadge && (
        <span className="approval-todo-btn__badge" aria-hidden="true">
          {formatTodoCount(count)}
        </span>
      )}
    </button>
  );
}
