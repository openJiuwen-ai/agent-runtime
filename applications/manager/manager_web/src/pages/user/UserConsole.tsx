import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../../components/LanguageSwitcher';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useAuth } from '../../auth/AuthContext';
import { useAsync } from '../../hooks/useAsync';
import { AgentTemplate, Org, UserConsoleApi } from '../../services/api';
import { getProductName } from '../../utils/env';

// 内嵌聊天(web_enterprise)的基址：默认同源 /chat（webui nginx 以 base=/chat/ 同源提供 dist，
// 不再指向 :5173，因而不会"localhost 拒绝访问"；其 SSE/file-api 走根路径，由 webui 反代到 web 后端）。
const CHAT_BASE = (import.meta.env.VITE_CHAT_BASE_URL as string | undefined) || '/chat';
const AUTH_EXPIRED_MESSAGE = 'jiuwenswarm:auth-expired';

/** 用户控制台里实例化 Agent 的运行时标识；回退 template_id。 */
function agentRuntimeId(agent: AgentTemplate): string {
  return agent.resource_id || agent.template_id;
}

export function UserConsole() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const { data: orgsData } = useAsync(() => UserConsoleApi.orgs(), []);
  const [orgId, setOrgId] = useState<string | null>(null);
  const orgs = orgsData?.orgs ?? [];
  const currentOrg = orgs.find((o) => o.group_id === orgId) ?? null;

  // iframe 与统一外壳同源；会话失效由外壳统一退出，User Web 不再展示第二套登录页。
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === AUTH_EXPIRED_MESSAGE) void logout();
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [logout]);

  return (
    // 自带高度链：100vh 的纵向 flex，topbar 固定高、body 占满剩余(min-height:0 关键)
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <header className="topbar" style={{ flexShrink: 0 }}>
        <div className="brand">
          <img src="/logo.png" alt={getProductName()} className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">{t('brand.title')}</span>
            <span className="brand-sub">{t('userConsole.brandSub')}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <span className="text-sm text-muted">{user?.display_name}</span>
          <button className="btn" onClick={() => void logout()}>{t('auth.logout')}</button>
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {!currentOrg ? (
          <OrgPicker orgs={orgs} onPick={setOrgId} />
        ) : (
          <OrgWorkspace org={currentOrg} userId={user?.user_id ?? ''} onSwitchOrg={() => setOrgId(null)} />
        )}
      </div>
    </div>
  );
}

function OrgPicker({ orgs, onPick }: { orgs: Org[]; onPick: (gid: string) => void }) {
  const { t } = useTranslation();
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px' }}>
      <h2 className="card-title mb-1">{t('userConsole.myOrgs')}</h2>
      <p className="text-sm text-muted mb-3">{t('userConsole.pickOrgFirst')}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {orgs.map((o) => (
          <button key={o.group_id} className="card" style={{ width: 200, textAlign: 'left', cursor: 'pointer' }} onClick={() => onPick(o.group_id)}>
            <div className="card-title">{o.name}</div>
            <div className="text-xs text-muted mono">{o.group_id}</div>
          </button>
        ))}
        {orgs.length === 0 && <div className="text-muted">{t('userConsole.noOrgs')}</div>}
      </div>
    </div>
  );
}

function OrgWorkspace({ org, userId, onSwitchOrg }: { org: Org; userId: string; onSwitchOrg: () => void }) {
  const { t } = useTranslation();
  const { data: agentsData, loading } = useAsync(() => UserConsoleApi.agents(org.group_id), [org.group_id]);
  const [agentId, setAgentId] = useState<string | null>(null);
  const agents = agentsData?.agents ?? [];
  const currentAgent = agents.find((a) => agentRuntimeId(a) === agentId) ?? null;

  // 切组织后重置选中的 agent
  useEffect(() => { setAgentId(null); }, [org.group_id]);

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* 左：组织信息 + agent 列表 */}
      <aside style={{ width: 240, borderRight: '1px solid var(--border, #e5e7eb)', padding: 12, overflowY: 'auto', flexShrink: 0 }}>
        <div className="flex items-center justify-between mb-2">
          <div className="card-title" style={{ fontSize: 14 }}>{org.name}</div>
          <button className="btn ghost sm" onClick={onSwitchOrg}>{t('userConsole.switchOrg')}</button>
        </div>
        <div className="nav-group-title nav-group-title--uppercase">{t('userConsole.availableBots')}</div>
        <div className="space-y-1">
          {agents.map((a) => (
            <button key={agentRuntimeId(a)} className={`nav-item ${agentId === agentRuntimeId(a) ? 'active' : ''}`} onClick={() => setAgentId(agentRuntimeId(a))}>
              {a.template_name}
            </button>
          ))}
          {!loading && agents.length === 0 && <div className="text-xs text-muted" style={{ padding: 8 }}>{t('userConsole.noBots')}</div>}
        </div>
      </aside>

      {/* 右：选中 agent 的工作区 */}
      <section style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex' }}>
        {currentAgent ? (
          <AgentWorkspace agent={currentAgent} userId={userId} groupId={org.group_id} />
        ) : (
          <div className="flex items-center justify-center" style={{ flex: 1 }}>
            <div className="text-muted">{t('userConsole.pickBot')}</div>
          </div>
        )}
      </section>
    </div>
  );
}

/** 聊天 iframe 加载中的遮罩：大旋转图标 + 说明；加载偏慢再补一行提示。 */
function ChatLoading() {
  const { t } = useTranslation();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), 12000);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <div
      style={{
        position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 14,
        background: 'var(--bg-content, var(--bg, #fff))',
      }}
    >
      <svg width="64" height="64" viewBox="0 0 50 50" aria-hidden="true">
        <circle cx="25" cy="25" r="20" fill="none" stroke="var(--border, #e5e7eb)" strokeWidth="4" />
        <path d="M25 5 a20 20 0 0 1 20 20" fill="none" stroke="var(--accent, #6366f1)" strokeWidth="4" strokeLinecap="round">
          <animateTransform attributeName="transform" type="rotate" from="0 25 25" to="360 25 25" dur="0.9s" repeatCount="indefinite" />
        </path>
      </svg>
      <div className="text-sm text-muted">{t('userConsole.chatLoading')}</div>
      {slow && <div className="text-xs text-muted" style={{ maxWidth: 320, textAlign: 'center' }}>{t('userConsole.chatLoadingSlow')}</div>}
    </div>
  );
}

function AgentWorkspace({ agent, userId, groupId }: { agent: AgentTemplate; userId: string; groupId: string }) {
  const { t, i18n } = useTranslation();
  // 内嵌 iframe(web_enterprise)与父窗口可能不同源,语言状态独立 → 用 postMessage 同步语言
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const postLang = useCallback(
    (el: HTMLIFrameElement | null) =>
      el?.contentWindow?.postMessage({ type: 'jw-set-lang', lang: i18n.language.startsWith('en') ? 'en' : 'zh' }, '*'),
    [i18n.language],
  );
  // 语言切换时,通知已加载的内嵌用户面
  useEffect(() => {
    postLang(iframeRef.current);
  }, [postLang]);

  // (user_id, group_id, bot_id) 经 query 注入 user_web → extSettings 读取 → HTTP/SSE 透传 → Agent
  // bot_id 传实例化 resource_id（运行时路由字段名未改）
  const runtimeId = agentRuntimeId(agent);
  const baseQuery = useMemo(
    () => new URLSearchParams({ user_id: userId, group_id: groupId, bot_id: runtimeId }).toString(),
    [userId, groupId, runtimeId],
  );
  const chatUrl = `${CHAT_BASE}/?${baseQuery}`;
  const [loaded, setLoaded] = useState(false);

  // 切换 Agent 或组织后，新的 iframe 重新进入加载态。
  useEffect(() => {
    setLoaded(false);
  }, [baseQuery]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid var(--border, #e5e7eb)', flexShrink: 0 }}>
        <span className="btn sm primary">{t('userConsole.tabChat')}</span>
        <div style={{ flex: 1 }} />
        <span className="text-xs text-muted" style={{ alignSelf: 'center' }}>agent: <span className="mono">{runtimeId}</span></span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <iframe
          key={baseQuery}
          ref={iframeRef}
          src={chatUrl}
          title="chat"
          onLoad={(e) => { setLoaded(true); postLang(e.currentTarget); }}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 0 }}
        />
        {!loaded && <ChatLoading />}
      </div>
    </div>
  );
}
