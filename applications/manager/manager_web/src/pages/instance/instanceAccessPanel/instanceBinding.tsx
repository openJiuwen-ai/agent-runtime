/** 实例访问面板复用的「添加到实例」选择器。 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { HintTooltip } from '../../../components/HintTooltip';
import { useFormDirty } from '../../../hooks/useFormDirty';
import { ApiError } from '../../../services/api';
import { toast } from '../../../stores/uiStore';

export interface Candidate { id: string; label: string; sub?: string }

export type AddToInstanceConfirmPayload = {
  ids: string[];
  expires_at: string | null;
  login_policy: 'allow' | 'deny';
};

/** 通用"添加到实例"选择器：候选（已排除在册者）搜索 + 多选 + 可选过期时间/登录权限 → onConfirm。 */
export function AddToInstanceModal({
  title, candidates, onConfirm, onClose, showExpiresAt = false,
}: {
  title: string;
  candidates: Candidate[];
  onConfirm: (payload: AddToInstanceConfirmPayload) => Promise<void>;
  onClose: () => void;
  showExpiresAt?: boolean;
}) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(true);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [loginPolicy, setLoginPolicy] = useState<'allow' | 'deny'>('allow');
  const [busy, setBusy] = useState(false);
  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return kw ? candidates.filter((c) => c.label.toLowerCase().includes(kw) || c.id.toLowerCase().includes(kw)) : candidates;
  }, [candidates, q]);

  useEffect(() => {
    markClean({ ids: [] as string[], expiresAt: '', loginPolicy: 'allow' as const });
  }, [markClean]);

  const draft = {
    ids: [...sel].sort(),
    expiresAt,
    loginPolicy,
  };

  function toggle(id: string) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function submit() {
    setBusy(true);
    try {
      await onConfirm({
        ids: Array.from(sel),
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
        login_policy: loginPolicy,
      });
      onClose();
    } catch (e) {
      toast('danger', e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      dirty={isDirty(draft)}
      footer={
        <>
          <ModalCancelButton className="btn" />
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || sel.size === 0} onClick={submit}>
            {t('common.confirm', { defaultValue: '确定' })}{sel.size ? `（${sel.size}）` : ''}
          </button>
        </>
      }
    >
      <input
        className="input"
        placeholder={t('common.search', { defaultValue: '搜索' })}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: 8 }}
      />
      <div style={{ maxHeight: 300, overflow: 'auto', border: '1px solid var(--border, #ddd)', borderRadius: 6, padding: 8 }}>
        {filtered.map((c) => (
          <label key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
            <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
            <span>{c.label}</span>
            {c.sub && <span className="text-xs text-muted mono">{c.sub}</span>}
          </label>
        ))}
        {filtered.length === 0 && (
          <div className="text-xs text-muted">{t('iam.noCandidates', { defaultValue: '没有可添加的候选（都已在该实例）' })}</div>
        )}
      </div>
      {showExpiresAt ? (
        <>
          <label className="block mt-3">
            <span className="text-sm font-medium inline-flex items-center gap-1">
              {t('instanceDetail.accessPanel.instance.loginPolicy', { defaultValue: '登录权限' })}
              <HintTooltip
                text={t('instanceDetail.accessPanel.instance.loginPolicyHint', {
                  defaultValue: '拒绝优先级高于允许',
                })}
              />
            </span>
            <select
              className="input mt-1 w-full"
              value={loginPolicy}
              onChange={(e) => setLoginPolicy(e.target.value === 'deny' ? 'deny' : 'allow')}
            >
              <option value="allow">
                {t('instanceDetail.accessPanel.instance.loginAllow', { defaultValue: '允许' })}
              </option>
              <option value="deny">
                {t('instanceDetail.accessPanel.instance.loginDeny', { defaultValue: '拒绝' })}
              </option>
            </select>
          </label>
          <label className="block mt-3">
            <span className="text-sm font-medium">
              {t('instanceDetail.accessPanel.instance.expiresAt', { defaultValue: '过期时间' })}
            </span>
            <input
              type="datetime-local"
              className="input mt-1 w-full cursor-pointer"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              onClick={(e) => (e.target as HTMLInputElement).showPicker?.()}
            />
            <div className="text-[11px] text-muted mt-1">
              {t('instanceDetail.accessPanel.instance.expiresHint', { defaultValue: '留空表示永不过期' })}
            </div>
          </label>
        </>
      ) : null}
    </Modal>
  );
}
