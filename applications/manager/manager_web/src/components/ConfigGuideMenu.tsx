import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { InstanceApi } from '../services/api';

/** 配置引导里选中的集群 ID 持久化 key，刷新页面后仍保持上次选择 */
const SELECTED_CLUSTER_KEY = 'claw_manager_guide_cluster';

/** 引导菜单里的宽按钮：整列宽度、左对齐，悬停显示箭头 */
function GuideButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" className="guide-btn" onClick={onClick} disabled={disabled}>
      <span className="truncate">{label}</span>
      <span className="guide-btn__arrow" aria-hidden="true">→</span>
    </button>
  );
}

/** 列标题：渐变装饰条 + 文字 */
function GuideTitle({ text }: { text: string }) {
  return (
    <div className="guide-menu__title">
      <span className="guide-menu__title-bar" aria-hidden="true" />
      {text}
    </div>
  );
}

/**
 * 顶栏「配置引导」下拉：新手开局 / 进阶配置 / 集群管理 三列跳转按钮，
 * 集群管理列选择集群后可直接跳到该集群的各页签。
 */
export function ConfigGuideMenu() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [open, setOpen] = useState(false);
  /** 上次选中的集群 ID（localStorage 持久化），仅在它仍存在于集群列表时生效 */
  const [selectedId, setSelectedId] = useState(
    () => localStorage.getItem(SELECTED_CLUSTER_KEY) ?? '',
  );
  const rootRef = useRef<HTMLDivElement | null>(null);

  const clusters = useAsync(() => InstanceApi.list({ page: 1, page_size: 200 }), []);
  const clusterItems = clusters.data?.items ?? [];

  /**
   * 选中项仍在列表里则保持不变（含刷新后恢复上次选择）；
   * 被删除或无存储时回落到第一个集群；无集群时清空显示「暂无集群」。
   */
  useEffect(() => {
    if (clusterItems.some((it) => it.jiuwenclaw_id === selectedId)) return;
    const fallback = clusterItems[0]?.jiuwenclaw_id ?? '';
    setSelectedId(fallback);
    if (fallback) {
      localStorage.setItem(SELECTED_CLUSTER_KEY, fallback);
    } else {
      localStorage.removeItem(SELECTED_CLUSTER_KEY);
    }
  }, [clusterItems, selectedId]);

  const handleSelectCluster = (id: string) => {
    setSelectedId(id);
    if (id) {
      localStorage.setItem(SELECTED_CLUSTER_KEY, id);
    } else {
      localStorage.removeItem(SELECTED_CLUSTER_KEY);
    }
  };

  // 打开时点击外部 / 按 Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) void clusters.reload();
  };

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  const startedItems = [
    { label: t('guide.newMember'), to: '/users' },
    { label: t('iam.newOrg'), to: '/orgs' },
    { label: t('guide.newModel'), to: '/model-templates' },
    { label: t('guide.newAgentDefinition'), to: '/agent-templates' },
    { label: t('guide.newPoolDefinition'), to: '/service-config-templates' },
    { label: t('topology.createInstance'), to: '/instances' },
  ];

  const advancedItems = [
    { label: t('nav.embeddingTemplates'), to: '/embedding-templates' },
    { label: t('nav.skillWhitelistTemplates'), to: '/skill-prebuilt-templates' },
    { label: t('nav.safetyGuardrails'), to: '/safety-guardrails' },
    { label: t('nav.a2aManagement'), to: '/a2a-management' },
    { label: t('nav.extensionTemplates'), to: '/extension-config-templates' },
  ];

  const clusterTabs = selectedId
    ? [
        { label: t('instanceDetail.tabs.access'), to: `/instances/${selectedId}/access` },
        { label: t('instanceDetail.tabs.clusterConfig'), to: `/instances/${selectedId}/cluster-config` },
        { label: t('instanceDetail.tabs.status'), to: `/instances/${selectedId}/status` },
        { label: t('instanceDetail.tabs.tokenQuota'), to: `/instances/${selectedId}/token-quota` },
        { label: t('instanceDetail.tabs.cost'), to: `/instances/${selectedId}/cost` },
        { label: t('instanceDetail.tabs.audit'), to: `/instances/${selectedId}/audit` },
      ]
    : [];

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className={`guide-trigger ${open ? 'guide-trigger--open' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        {/* 四方块网格：配置引导 */}
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5} aria-hidden>
          <rect x="4" y="4" width="6.5" height="6.5" rx="1.5" />
          <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" />
          <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" />
          <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" />
        </svg>
        {t('guide.configGuide')}
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={2}
          aria-hidden
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="guide-menu" role="menu">
          <div className="guide-menu__col">
            <GuideTitle text={t('guide.gettingStarted')} />
            {startedItems.map((it) => (
              <GuideButton key={it.to + it.label} label={it.label} onClick={() => go(it.to)} />
            ))}
          </div>

          <div className="guide-menu__col">
            <GuideTitle text={t('nav.instances')} />
            {clusterItems.length === 0 ? (
              <div className="text-xs text-muted py-2">{t('guide.noClusters')}</div>
            ) : (
              <>
                <div className="guide-menu__cluster-row">
                  <span className="guide-menu__cluster-label">{t('guide.selectCluster')}：</span>
                  <select
                    className="select"
                    aria-label={t('guide.selectCluster')}
                    value={selectedId}
                    onChange={(e) => handleSelectCluster(e.target.value)}
                  >
                    {clusterItems.map((it) => (
                      <option key={it.jiuwenclaw_id} value={it.jiuwenclaw_id}>
                        {it.jiuwenclaw_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="guide-menu__cluster-grid">
                  {clusterTabs.map((it) => (
                    <GuideButton key={it.to} label={it.label} onClick={() => go(it.to)} />
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="guide-menu__col">
            <GuideTitle text={t('guide.advancedConfig')} />
            {advancedItems.map((it) => (
              <GuideButton key={it.to + it.label} label={it.label} onClick={() => go(it.to)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
