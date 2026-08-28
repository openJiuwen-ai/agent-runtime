import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "../../components/LanguageSwitcher";
import { ThemeToggle } from "../../components/ThemeToggle";
import { useAuth } from "../../auth/AuthContext";
import { useAsync } from "../../hooks/useAsync";
import {
  AgentTemplate,
  Org,
  UserConsoleApi,
  UserGateway,
} from "../../services/api";
import { getProductName } from "../../utils/env";

// 内嵌 User Web 的基址。默认通过 Manager Web 的同源 /chat 反向代理加载。
const CHAT_BASE =
  (import.meta.env.VITE_CHAT_BASE_URL as string | undefined) || "/chat";
const AUTH_EXPIRED_MESSAGE = "jiuwenswarm:auth-expired";
const USER_CONTEXT_KEY = "jiuwenswarm:user-context";
const CONTEXT_READY_MESSAGE = "jiuwenswarm:enterprise-context-ready";
const CONTEXT_SNAPSHOT_MESSAGE = "jiuwenswarm:enterprise-context-snapshot";
const CONTEXT_CHANGE_MESSAGE = "jiuwenswarm:enterprise-context-change";
const CONTEXT_LOGOUT_MESSAGE = "jiuwenswarm:enterprise-context-logout";

/** 用户控制台里实例化 Agent 的运行时标识；回退 template_id。 */
function agentRuntimeId(agent: AgentTemplate): string {
  return agent.resource_id || agent.template_id;
}

export function UserConsole() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const { data: orgsData } = useAsync(() => UserConsoleApi.orgs(), []);
  const { data: gatewaysData, loading: gatewaysLoading } = useAsync(
    () => UserConsoleApi.gateways(),
    [],
  );
  const [orgId, setOrgId] = useState<string | null>(null);
  const [gatewayId, setGatewayId] = useState<string | null>(() =>
    sessionStorage.getItem("jw_user_gateway_id"),
  );
  const saveGateway = useCallback(
    (gateway: UserGateway) => {
      setGatewayId(gateway.jiuwenclaw_id);
      sessionStorage.setItem("jw_user_gateway_id", gateway.jiuwenclaw_id);
      sessionStorage.setItem(
        USER_CONTEXT_KEY,
        JSON.stringify({
          user_id: user?.user_id ?? "",
          gateway_id: gateway.jiuwenclaw_id,
          gateway_endpoint: gateway.gateway_endpoint,
        }),
      );
    },
    [user?.user_id],
  );
  const gateways = gatewaysData?.gateways ?? [];
  const orgs = orgsData?.orgs ?? [];
  const currentOrg = orgs.find((o) => o.group_id === orgId) ?? null;
  const currentGateway =
    gateways.find((g) => g.jiuwenclaw_id === gatewayId) ?? null;
  const selectOrg = useCallback(
    (id: string) => {
      if (orgs.some((org) => org.group_id === id)) setOrgId(id);
    },
    [orgs],
  );
  const selectGateway = useCallback(
    (id: string) => {
      const gateway = gateways.find((item) => item.jiuwenclaw_id === id);
      if (!gateway) return;
      saveGateway(gateway);
      setOrgId(null);
    },
    [gateways, saveGateway],
  );
  useEffect(() => {
    if (!gatewayId && gateways.length === 1) {
      setGatewayId(gateways[0].jiuwenclaw_id);
      saveGateway(gateways[0]);
    }
  }, [gatewayId, gateways, saveGateway]);

  // iframe 与统一外壳同源；会话失效由外壳统一退出，User Web 不再展示第二套登录页。
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === AUTH_EXPIRED_MESSAGE) void logout();
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [logout]);

  return (
    // 自带高度链：100vh 的纵向 flex，topbar 固定高、body 占满剩余(min-height:0 关键)
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        overflow: "hidden",
      }}
    >
      <header className="topbar" style={{ flexShrink: 0 }}>
        <div className="brand">
          <img
            src="/logo.png"
            alt={getProductName()}
            className="brand-logo-img"
          />
          <div className="brand-text">
            <span className="brand-title">{t("brand.title")}</span>
            <span className="brand-sub">{t("userConsole.brandSub")}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <span className="text-sm text-muted">{user?.display_name}</span>
          <button className="btn" onClick={() => void logout()}>
            {t("auth.logout")}
          </button>
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {!currentGateway ? (
          <GatewayPicker
            gateways={gateways}
            loading={gatewaysLoading}
            onPick={(id) => {
              const gateway = gateways.find(
                (item) => item.jiuwenclaw_id === id,
              );
              if (gateway) saveGateway(gateway);
            }}
          />
        ) : !currentOrg ? (
          <OrgPicker orgs={orgs} onPick={setOrgId} />
        ) : (
          <OrgWorkspace
            org={currentOrg}
            orgs={orgs}
            gateway={currentGateway}
            gateways={gateways}
            userId={user?.user_id ?? ""}
            userDisplayName={user?.display_name ?? user?.user_id ?? ""}
            onOrgChange={selectOrg}
            onGatewayChange={selectGateway}
            onLogout={() => void logout()}
            onSwitchOrg={() => setOrgId(null)}
            onSwitchGateway={() => {
              setGatewayId(null);
              sessionStorage.removeItem("jw_user_gateway_id");
              sessionStorage.removeItem(USER_CONTEXT_KEY);
              setOrgId(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function GatewayPicker({
  gateways,
  loading,
  onPick,
}: {
  gateways: UserGateway[];
  loading: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
      <h2 className="card-title mb-1">选择组网</h2>
      <p className="text-sm text-muted mb-3">
        登录用户只能看到管理面授权的组网。
      </p>
      {loading ? (
        <div className="text-muted">加载中...</div>
      ) : gateways.length === 0 ? (
        <div className="text-muted">暂无可用组网</div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          {gateways.map((g) => (
            <button
              key={g.jiuwenclaw_id}
              className="card"
              style={{ width: 260, textAlign: "left", cursor: "pointer" }}
              onClick={() => onPick(g.jiuwenclaw_id)}
            >
              <div className="card-title">{g.jiuwenclaw_name}</div>
              <div className="text-xs text-muted mono">{g.jiuwenclaw_id}</div>
              <div className="text-xs text-muted">
                {g.status} · {g.gateway_endpoint || "endpoint 未注册"}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function OrgPicker({
  orgs,
  onPick,
}: {
  orgs: Org[];
  onPick: (gid: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
      <h2 className="card-title mb-1">{t("userConsole.myOrgs")}</h2>
      <p className="text-sm text-muted mb-3">{t("userConsole.pickOrgFirst")}</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        {orgs.map((o) => (
          <button
            key={o.group_id}
            className="card"
            style={{ width: 200, textAlign: "left", cursor: "pointer" }}
            onClick={() => onPick(o.group_id)}
          >
            <div className="card-title">{o.name}</div>
            <div className="text-xs text-muted mono">{o.group_id}</div>
          </button>
        ))}
        {orgs.length === 0 && (
          <div className="text-muted">{t("userConsole.noOrgs")}</div>
        )}
      </div>
    </div>
  );
}

function OrgWorkspace({
  org,
  orgs,
  gateway,
  gateways,
  userId,
  userDisplayName,
  onOrgChange,
  onGatewayChange,
  onLogout,
  onSwitchOrg,
  onSwitchGateway,
}: {
  org: Org;
  orgs: Org[];
  gateway: UserGateway;
  gateways: UserGateway[];
  userId: string;
  userDisplayName: string;
  onOrgChange: (id: string) => void;
  onGatewayChange: (id: string) => void;
  onLogout: () => void;
  onSwitchOrg: () => void;
  onSwitchGateway: () => void;
}) {
  const { t } = useTranslation();
  const { data: agentsData, loading } = useAsync(
    () => UserConsoleApi.agents(org.group_id, gateway.jiuwenclaw_id),
    [org.group_id, gateway.jiuwenclaw_id],
  );
  const [agentId, setAgentId] = useState<string | null>(null);
  const agents = agentsData?.agents ?? [];
  const currentAgent =
    agents.find((a) => agentRuntimeId(a) === agentId) ?? null;

  // 切组织后重置选中的 agent
  useEffect(() => {
    setAgentId(null);
  }, [org.group_id]);

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      {/* 左：组织信息 + agent 列表 */}
      <aside
        style={{
          width: 240,
          borderRight: "1px solid var(--border, #e5e7eb)",
          padding: 12,
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        <div className="flex items-center justify-between mb-2">
          <div className="card-title" style={{ fontSize: 14 }}>
            {org.name}
          </div>
          <button className="btn ghost sm" onClick={onSwitchOrg}>
            {t("userConsole.switchOrg")}
          </button>
          <button className="btn ghost sm" onClick={onSwitchGateway}>
            切换组网
          </button>
        </div>
        <div className="nav-group-title nav-group-title--uppercase">
          {t("userConsole.availableBots")}
        </div>
        <div className="space-y-1">
          {agents.map((a) => (
            <button
              key={agentRuntimeId(a)}
              className={`nav-item ${agentId === agentRuntimeId(a) ? "active" : ""}`}
              onClick={() => setAgentId(agentRuntimeId(a))}
            >
              {a.template_name}
            </button>
          ))}
          {!loading && agents.length === 0 && (
            <div className="text-xs text-muted" style={{ padding: 8 }}>
              {t("userConsole.noBots")}
            </div>
          )}
        </div>
      </aside>

      {/* 右：选中 agent 的工作区 */}
      <section style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex" }}>
        {currentAgent ? (
          <AgentWorkspace
            agent={currentAgent}
            agents={agents}
            org={org}
            orgs={orgs}
            gateway={gateway}
            gateways={gateways}
            userId={userId}
            userDisplayName={userDisplayName}
            onAgentChange={setAgentId}
            onOrgChange={onOrgChange}
            onGatewayChange={onGatewayChange}
            onLogout={onLogout}
          />
        ) : (
          <div className="flex items-center justify-center" style={{ flex: 1 }}>
            <div className="text-muted">{t("userConsole.pickBot")}</div>
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
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        background: "var(--bg-content, var(--bg, #fff))",
      }}
    >
      <svg width="64" height="64" viewBox="0 0 50 50" aria-hidden="true">
        <circle
          cx="25"
          cy="25"
          r="20"
          fill="none"
          stroke="var(--border, #e5e7eb)"
          strokeWidth="4"
        />
        <path
          d="M25 5 a20 20 0 0 1 20 20"
          fill="none"
          stroke="var(--accent, #6366f1)"
          strokeWidth="4"
          strokeLinecap="round"
        >
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 25 25"
            to="360 25 25"
            dur="0.9s"
            repeatCount="indefinite"
          />
        </path>
      </svg>
      <div className="text-sm text-muted">{t("userConsole.chatLoading")}</div>
      {slow && (
        <div
          className="text-xs text-muted"
          style={{ maxWidth: 320, textAlign: "center" }}
        >
          {t("userConsole.chatLoadingSlow")}
        </div>
      )}
    </div>
  );
}

function AgentWorkspace({
  agent,
  agents,
  org,
  orgs,
  gateway,
  gateways,
  userId,
  userDisplayName,
  onAgentChange,
  onOrgChange,
  onGatewayChange,
  onLogout,
}: {
  agent: AgentTemplate;
  agents: AgentTemplate[];
  org: Org;
  orgs: Org[];
  gateway: UserGateway;
  gateways: UserGateway[];
  userId: string;
  userDisplayName: string;
  onAgentChange: (id: string) => void;
  onOrgChange: (id: string) => void;
  onGatewayChange: (id: string) => void;
  onLogout: () => void;
}) {
  const { t, i18n } = useTranslation();
  // 内嵌 iframe(web_enterprise)与父窗口可能不同源,语言状态独立 → 用 postMessage 同步语言
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const postLang = useCallback(
    (el: HTMLIFrameElement | null) =>
      el?.contentWindow?.postMessage(
        {
          type: "jw-set-lang",
          lang: i18n.language.startsWith("en") ? "en" : "zh",
        },
        "*",
      ),
    [i18n.language],
  );
  // 语言切换时,通知已加载的内嵌用户面
  useEffect(() => {
    postLang(iframeRef.current);
  }, [postLang]);

  // (user_id, group_id, bot_id) 经 query 注入 User Web，再通过构建时选定的 WS 或 HTTP/SSE 通道传给 Gateway。
  // bot_id 传实例化 resource_id（运行时路由字段名未改）
  const runtimeId = agentRuntimeId(agent);
  const baseQuery = useMemo(
    () =>
      new URLSearchParams({
        user_id: userId,
        group_id: org.group_id,
        bot_id: runtimeId,
        gateway_id: gateway.jiuwenclaw_id,
      }).toString(),
    [userId, org.group_id, runtimeId, gateway.jiuwenclaw_id],
  );
  const chatUrl = `${CHAT_BASE}/?${baseQuery}`;
  const chatOrigin = useMemo(
    () => new URL(chatUrl, window.location.href).origin,
    [chatUrl],
  );
  const [loaded, setLoaded] = useState(false);
  const contextSnapshot = useMemo(
    () => ({
      user: { user_id: userId, display_name: userDisplayName },
      org: { group_id: org.group_id, name: org.name },
      orgs: orgs.map((item) => ({ group_id: item.group_id, name: item.name })),
      gateway: {
        jiuwenclaw_id: gateway.jiuwenclaw_id,
        jiuwenclaw_name: gateway.jiuwenclaw_name,
        gateway_endpoint: gateway.gateway_endpoint,
      },
      gateways: gateways.map((item) => ({
        jiuwenclaw_id: item.jiuwenclaw_id,
        jiuwenclaw_name: item.jiuwenclaw_name,
        gateway_endpoint: item.gateway_endpoint,
      })),
      agents: agents.map((item) => ({
        template_id: item.template_id,
        template_name: item.template_name,
        resource_id: item.resource_id,
      })),
      selectedBot: runtimeId,
    }),
    [
      agent,
      agents,
      gateway,
      gateways,
      org,
      orgs,
      runtimeId,
      userDisplayName,
      userId,
    ],
  );
  const postContext = useCallback(
    (target?: WindowProxy | null) => {
      const receiver = target ?? iframeRef.current?.contentWindow;
      receiver?.postMessage(
        { type: CONTEXT_SNAPSHOT_MESSAGE, context: contextSnapshot },
        chatOrigin,
      );
    },
    [chatOrigin, contextSnapshot],
  );

  useEffect(() => {
    const handleContextMessage = (event: MessageEvent) => {
      if (
        event.source !== iframeRef.current?.contentWindow ||
        event.origin !== chatOrigin
      )
        return;
      if (!event.data || typeof event.data !== "object") return;
      if (event.data.type === CONTEXT_READY_MESSAGE) {
        postContext(iframeRef.current?.contentWindow);
        return;
      }
      if (event.data.type === CONTEXT_LOGOUT_MESSAGE) {
        onLogout();
        return;
      }
      if (
        event.data.type !== CONTEXT_CHANGE_MESSAGE ||
        typeof event.data.field !== "string" ||
        typeof event.data.value !== "string"
      ) {
        return;
      }
      const value = event.data.value;
      if (
        event.data.field === "group_id" &&
        orgs.some((item) => item.group_id === value)
      ) {
        onOrgChange(value);
      } else if (
        event.data.field === "gateway_id" &&
        gateways.some((item) => item.jiuwenclaw_id === value)
      ) {
        onGatewayChange(value);
      } else if (
        event.data.field === "bot_id" &&
        agents.some((item) => agentRuntimeId(item) === value)
      ) {
        onAgentChange(value);
      }
    };
    window.addEventListener("message", handleContextMessage);
    return () => window.removeEventListener("message", handleContextMessage);
  }, [
    agents,
    chatOrigin,
    gateways,
    onAgentChange,
    onGatewayChange,
    onLogout,
    onOrgChange,
    orgs,
    postContext,
  ]);

  useEffect(() => {
    postContext();
  }, [postContext]);

  // 切换 Agent 或组织后，新的 iframe 重新进入加载态。
  useEffect(() => {
    setLoaded(false);
  }, [baseQuery]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border, #e5e7eb)",
          flexShrink: 0,
        }}
      >
        <span className="btn sm primary">{t("userConsole.tabChat")}</span>
        <div style={{ flex: 1 }} />
        <span className="text-xs text-muted" style={{ alignSelf: "center" }}>
          agent: <span className="mono">{runtimeId}</span>
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <iframe
          key={baseQuery}
          ref={iframeRef}
          src={chatUrl}
          title="chat"
          onLoad={(e) => {
            setLoaded(true);
            postLang(e.currentTarget);
            postContext(e.currentTarget.contentWindow);
          }}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            border: 0,
          }}
        />
        {!loaded && <ChatLoading />}
      </div>
    </div>
  );
}
