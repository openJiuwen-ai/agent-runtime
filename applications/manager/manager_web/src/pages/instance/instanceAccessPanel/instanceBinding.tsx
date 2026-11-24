/** 实例访问面板复用的「添加到实例」选择器。 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { HintTooltip } from '../../../components/HintTooltip';
import { useRouter } from '../../../router';
import { useFormDirty } from '../../../hooks/useFormDirty';
import { ApiError } from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import { type AdmissionDraft, emptyAdmissionDraft } from '../../../utils/admissionNavigation';
import './instanceBinding.css';

export interface Candidate { id: string; label: string; sub?: string }

export type AddToInstanceConfirmPayload = {
  ids: string[];
  expires_at: string | null;
  login_policy: 'allow' | 'deny';
};

/** 通用"添加到实例"选择器：候选（已排除在册者）搜索 + 多选 + 可选过期时间/登录权限 → onConfirm。 */
export function AddToInstanceModal({
  title, candidates, onConfirm, onClose, showExpiresAt = false, manageLink, initialDraft = emptyAdmissionDraft,
  subjectKind, directoryCount, loading = false, error, onReload,
}: {
  title: string;
  candidates: Candidate[];
  subjectKind: 'users' | 'orgs';
  directoryCount: number;
  loading?: boolean;
  error?: string | null;
  onReload?: () => void;
  onConfirm: (payload: AddToInstanceConfirmPayload) => Promise<void>;
  onClose: () => void;
  showExpiresAt?: boolean;
  /** 目录跳转前保存未完成的准入选择，返回后继续添加。 */
  manageLink?: { page: string; to: string; onPrepare?: (draft: AdmissionDraft) => void };
  initialDraft?: AdmissionDraft;
}) {
  const { t, i18n } = useTranslation();
  const { navigate } = useRouter();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const subject = subjectKind === 'orgs' ? tr('组织', 'organizations') : tr('用户', 'users');
  const { markClean, isDirty } = useFormDirty(true);
  const [sel, setSel] = useState<Set<string>>(() => new Set(initialDraft.ids));
  const [q, setQ] = useState('');
  const [expiresAt, setExpiresAt] = useState(initialDraft.expiresAt);
  const [loginPolicy, setLoginPolicy] = useState<'allow' | 'deny'>(initialDraft.loginPolicy);
  const [busy, setBusy] = useState(false);
  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return kw ? candidates.filter((c) => c.label.toLowerCase().includes(kw) || c.id.toLowerCase().includes(kw)) : candidates;
  }, [candidates, q]);
  const selectedIds = candidates.filter(candidate => sel.has(candidate.id)).map(candidate => candidate.id);
  const canSelect = !loading && !error && candidates.length > 0;
  const openDirectory = () => {
    if (!manageLink) return;
    if (manageLink.onPrepare) manageLink.onPrepare(draft); else onClose();
    navigate(manageLink.to);
  };

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
    if (busy || !canSelect || selectedIds.length === 0) return;
    setBusy(true);
    try {
      await onConfirm({
        ids: selectedIds,
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
          <button className="btn primary" style={{ marginLeft: 8 }} disabled={busy || !canSelect || selectedIds.length === 0} onClick={submit}>
            {t('common.confirm', { defaultValue: '确定' })}{selectedIds.length ? `（${selectedIds.length}）` : ''}
          </button>
        </>
      }
    >
      {loading ? <div className="admission-picker-empty" role="status">{tr('正在读取可添加的', 'Loading available ')}{subject}…</div>
        : error ? <div className="admission-picker-empty" role="alert"><strong>{tr('无法读取', 'Could not load ')}{subject}</strong><p>{error}</p><button className="btn" onClick={onReload}>{tr('重试', 'Retry')}</button></div>
        : candidates.length === 0 ? <div className="admission-picker-empty">
          <span className="admission-picker-empty__icon" aria-hidden="true">＋</span>
          <h3>{directoryCount === 0 ? tr(`还没有${subject}，先到管理页创建`, `Create ${subject} to get started`) : tr(`所有${subject}都已添加到此集群`, `All ${subject} are already added`)}</h3>
          <p>{directoryCount === 0 ? tr(`创建${subject}后，点击管理页的“返回集群准入”即可继续添加。`, 'Create an entry, then use “Return to cluster admission” to continue.') : tr(`可关闭弹窗调整已有准入规则，或前往管理页创建新的${subject}。`, 'Close this dialog to edit existing rules, or create another entry in the directory.')}</p>
          {manageLink && <button className="btn primary" onClick={openDirectory}>{tr(`前往${manageLink.page}`, `Open ${manageLink.page}`)} →</button>}
        </div> : <>
        <div className="admission-picker-toolbar"><div><strong>{tr(`选择要添加的${subject}`, `Select ${subject}`)}</strong><p>{tr(`找不到${subject}？可先到管理页创建，再返回继续。`, 'Missing an entry? Create it in the directory, then return here.')}</p></div>{manageLink && <button className="btn" onClick={openDirectory}>{tr(`前往${manageLink.page}`, `Open ${manageLink.page}`)} →</button>}</div>
        <input className="input w-full mb-2" aria-label={tr(`搜索${subject}`, `Search ${subject}`)} placeholder={tr(`搜索${subject}名称或 ID`, `Search ${subject} by name or ID`)} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="admission-picker-list">
        {filtered.map((c) => (
          <label key={c.id}>
            <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
            <span className="min-w-0"><span className="block break-words">{c.label}</span>{c.sub && <span className="block text-xs text-muted mono break-all">{c.sub}</span>}</span>
          </label>
        ))}
        {filtered.length === 0 && (
          <div className="admission-picker-empty"><p>{tr(`没有匹配“${q}”的${subject}`, `No ${subject} match “${q}”`)}</p><button className="btn sm" onClick={() => setQ('')}>{tr('清空搜索', 'Clear search')}</button></div>
        )}
      </div>
      <p className="text-xs text-muted mt-2">{tr(`已选择 ${selectedIds.length} 项 · 前往管理页时保留当前选择和登录设置`, `${selectedIds.length} selected · Your selection and login settings are kept while you visit the directory`)}</p>
      </>}
      {showExpiresAt && canSelect ? (
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
              className="select mt-1 w-full"
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
