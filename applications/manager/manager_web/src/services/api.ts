import type {
  CreateInstanceBody,
  EmbeddingTemplate,
  EmbeddingTemplateCreateBody,
  EmbeddingTemplateUpdateBody,
  ExtensionConfigTemplate,
  ExtensionConfigTemplateCreateBody,
  ExtensionConfigTemplateUpdateBody,
  InstanceDetail,
  InstanceSummary,
  ManagerWsStatus,
  ModelTemplate,
  ModelTemplateCreateBody,
  ModelTemplateUpdateBody,
  PageResult,
  SkillWhitelistTemplate,
  SkillWhitelistTemplateCreateBody,
  SkillWhitelistTemplateUpdateBody,
  PermissionsTemplate,
  PermissionsTemplateCreateBody,
  PermissionsTemplateUpdateBody,
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
  ServiceConfigTemplateUpdateBody,
  ResponseModel,
  LogMaskingRule,
  LogMaskingRuleCreateBody,
  LogMaskingRuleUpdateBody,
  ListItemsResult,
  LoggingConfig,
  LoggingConfigUpsertBody,
} from '../types';

// 平台管理 API(claw_manager) 与 认证/目录 API(独立认证服务) 两个反代前缀。
function browserSafeBase(value: string | undefined, fallback: string): string {
  const candidate = (value ?? fallback).trim().replace(/\/$/, '');
  // API 请求必须经过 Manager Web 的同源反代；Kubernetes Service DNS 对浏览器不可见。
  if (candidate === fallback || candidate.startsWith(`${fallback}/`)) return candidate;
  if (typeof window !== 'undefined') {
    try {
      const url = new URL(candidate, window.location.origin);
      if (url.origin === window.location.origin && (url.pathname === fallback || url.pathname.startsWith(`${fallback}/`))) {
        return url.pathname.replace(/\/$/, '') || fallback;
      }
    } catch { /* 回退到同源前缀 */ }
    return fallback;
  }
  return candidate;
}

const API_BASE = browserSafeBase(import.meta.env.VITE_API_BASE, '/api');
const IDP_BASE = browserSafeBase(import.meta.env.VITE_IDP_BASE, '/idp');

// ---------- 认证 token（access JWT + refresh，localStorage 持久化）----------
const ACCESS_KEY = 'openjiuwen_access_token';
const REFRESH_KEY = 'openjiuwen_refresh_token';
let accessToken: string | null = localStorage.getItem(ACCESS_KEY);
let refreshToken: string | null = localStorage.getItem(REFRESH_KEY);
let unauthorizedHandler: (() => void) | null = null;

function syncAccessCookie(access: string | null): void {
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  if (access) {
    document.cookie = `${ACCESS_KEY}=${encodeURIComponent(access)}; Path=/; SameSite=Strict${secure}`;
  } else {
    document.cookie = `${ACCESS_KEY}=; Path=/; Max-Age=0; SameSite=Strict${secure}`;
  }
}

syncAccessCookie(accessToken);

export function setTokens(access: string | null, refresh?: string | null): void {
  accessToken = access;
  if (access) localStorage.setItem(ACCESS_KEY, access);
  else localStorage.removeItem(ACCESS_KEY);
  syncAccessCookie(access);
  if (refresh !== undefined) {
    refreshToken = refresh;
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
    else localStorage.removeItem(REFRESH_KEY);
  }
}
export function clearTokens(): void {
  setTokens(null, null);
}
export function getAccessToken(): string | null {
  return accessToken;
}
export function hasSession(): boolean {
  return !!accessToken;
}
/** 注册"会话失效(401)"回调：由 AuthProvider 设置为登出并回到登录页。 */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

interface FastApiValidationErrorItem {
  type?: string;
  loc?: unknown[];
  msg?: string;
}

/** 将 FastAPI / Pydantic 的 detail（string | object[]）转为可读文案。 */
export function formatApiErrorDetail(detail: unknown): string {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim();
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as FastApiValidationErrorItem).msg || '')
            .trim()
            .replace(/^Value error,\s*/i, '');
        }
        return '';
      })
      .filter(Boolean);
    return messages.join('；') || '请求参数校验失败';
  }
  if (typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === 'string') return obj.message.trim();
    if (typeof obj.msg === 'string') return obj.msg.trim();
  }
  return String(detail);
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public raw?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined | string[]>;
}

function buildQuery(query?: RequestOptions['query']) {
  if (!query) return '';
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      // 数组 → 重复参数（后端 FastAPI Query(list) 读 ?k=a&k=b）。
      for (const item of v) if (item !== '') usp.append(k, String(item));
    } else {
      usp.append(k, String(v));
    }
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

/** 用 refresh token 续期一次（成功则写入新 token）。 */
async function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return false;
  try {
    const resp = await fetch(`${IDP_BASE}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) return false;
    const t = (await resp.json()) as TokenResponse;
    setTokens(t.access_token, t.refresh_token);
    return true;
  } catch {
    return false;
  }
}

function resolveRequestUrl(base: string, path: string, query: string): string {
  // 浏览器永远只能访问当前 Manager Web 的同源反代；Kubernetes Service DNS
  // 只允许由 Manager Web/Vite 服务端使用，绝不能泄漏到浏览器。
  if (typeof window === 'undefined') return `${base}${path}${query}`;
  const prefix = base.includes('/idp') ? '/idp' : '/api';
  return `${window.location.origin}${prefix}${path}${query}`;
}

async function requestCore<T>(
  base: string, path: string, opts: RequestOptions, unwrap: boolean, retried = false,
): Promise<T> {
  const url = resolveRequestUrl(base, path, buildQuery(opts.query));
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const init: RequestInit = { method: opts.method ?? 'GET', headers };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }

  // 会话失效：401 且不是 token/refresh 端点 → 先试 refresh 续期重试一次,失败再全局登出。
  if (
    resp.status === 401 && accessToken && !retried &&
    !path.includes('/auth/token') && !path.includes('/auth/refresh')
  ) {
    if (await tryRefresh()) return requestCore<T>(base, path, opts, unwrap, true);
    unauthorizedHandler?.();
  }

  let json: unknown = null;
  const text = await resp.text();
  if (text) {
    try { json = JSON.parse(text); } catch { /* 非 JSON 响应 */ }
  }
  if (!resp.ok) {
    const rawDetail =
      json && typeof json === 'object' && 'detail' in (json as Record<string, unknown>)
        ? (json as { detail: unknown }).detail
        : undefined;
    throw new ApiError(resp.status, formatApiErrorDetail(rawDetail) || resp.statusText, json);
  }
  // manager API 返回 ResponseModel<T> 包装；认证服务返回原始 JSON(unwrap=false)。
  if (unwrap && json && typeof json === 'object' && 'code' in (json as Record<string, unknown>) && 'data' in (json as Record<string, unknown>)) {
    const wrapped = json as ResponseModel<T>;
    if (wrapped.code !== 200) {
      throw new ApiError(resp.status, wrapped.message || 'unknown error', json);
    }
    return wrapped.data as T;
  }
  return json as T;
}

/** 平台管理 API(claw_manager, /api)——拆 ResponseModel。 */
function http<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return requestCore<T>(API_BASE, path, opts, true);
}
/** 认证/目录 API(独立认证服务, /idp)——原始 JSON。 */
function idpHttp<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return requestCore<T>(IDP_BASE, path, opts, false);
}

// ---------- System ----------

export const SystemApi = {
  health: () => http<{ status: string }>('/health'),
  managerWsStatus: () => http<ManagerWsStatus>('/manager-ws/status'),
};

// ---------- Auth ----------

export interface AuthUser {
  user_id: string;
  display_name: string;
  is_admin: boolean;
  status: string;
  groups?: string[];
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token: string;
}
export interface FederationConnection {
  connection_id: string;
  name: string;
}

// 认证全部走独立认证服务(经 /idp 反代)。claw_manager 不再有登录端点。
export const AuthApi = {
  /** OAuth2 密码流：表单 POST /token → 存 access+refresh，再取 /me 返回用户。 */
  login: async (username: string, password: string): Promise<AuthUser> => {
    const body = new URLSearchParams({ username, password });
    const resp = await fetch(`${IDP_BASE}/v1/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const j = (await resp.json()) as { detail?: unknown };
        detail = formatApiErrorDetail(j?.detail) || detail;
      } catch { /* 非 JSON */ }
      throw new ApiError(resp.status, detail);
    }
    const t = (await resp.json()) as TokenResponse;
    setTokens(t.access_token, t.refresh_token);
    return idpHttp<AuthUser>('/v1/auth/me');
  },
  federationConnections: () =>
    idpHttp<{ connections: FederationConnection[] }>('/v1/auth/federation/connections'),
  beginFederatedLogin: (connectionId: string): void => {
    const connection = encodeURIComponent(connectionId);
    window.location.assign(
      `${IDP_BASE}/v1/auth/federation/${connection}/login?return_to=${encodeURIComponent('/auth')}`,
    );
  },
  exchangeFederationCode: async (code: string): Promise<AuthUser> => {
    const t = await idpHttp<TokenResponse>('/v1/auth/federation/exchange', {
      method: 'POST',
      body: { code },
    });
    setTokens(t.access_token, t.refresh_token);
    return idpHttp<AuthUser>('/v1/auth/me');
  },
  me: () => idpHttp<AuthUser>('/v1/auth/me'),
  myOrgs: () => idpHttp<{ orgs: Org[] }>('/v1/auth/me/orgs'),
  logout: async (): Promise<void> => {
    try {
      if (refreshToken) {
        await idpHttp('/v1/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } });
      }
    } catch { /* 忽略登出请求错误 */ }
    clearTokens();
  },
};

// ---------- IAM（组织 / 用户 / Agent 模板）----------

export interface Org {
  group_id: string;
  name: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}
export interface IamUser {
  user_id: string;
  display_name: string;
  is_admin: boolean;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  group_ids?: string[];
}
export type MatchExpr = string | string[];
/** instance_agent_resource 表一行（授权即实例化）。 */
export interface InstanceAgentResourceRecord {
  id: number;
  jiuwenclaw_id: string;
  resource_id: string;
  resource_name: string | null;
  resource_desc: string | null;
  ref_template_id: string;
  match_expr: MatchExpr;
  granted_by: string | null;
  expires_at: string | null;
  enabled: boolean;
  data: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}
export interface AgentTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description: string | null;
  agent_tags: string[] | null;
  template_ref: Record<string, string[]>;
  enabled: boolean;
  data: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
  resource_id?: string;
  ref_template_id?: string;
  /** 该模板关联的 instance_agent_resource 行 */
  records?: InstanceAgentResourceRecord[];
}
/** @deprecated 使用 AgentTemplate */
export type Bot = AgentTemplate;
interface IamPaged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const OrgApi = {
  list: (page = 1, page_size = 200) => idpHttp<IamPaged<Org>>('/v1/orgs/', { query: { page, page_size } }),
  create: (body: { group_id?: string; name: string }) => idpHttp<Org>('/v1/orgs/', { method: 'POST', body }),
  update: (gid: string, body: { name?: string; status?: string }) =>
    idpHttp<Org>(`/v1/orgs/${encodeURIComponent(gid)}`, { method: 'PATCH', body }),
  remove: (gid: string) => idpHttp<{ deleted: boolean }>(`/v1/orgs/${encodeURIComponent(gid)}`, { method: 'DELETE' }),
  listMembers: (gid: string) => idpHttp<{ users: IamUser[] }>(`/v1/orgs/${encodeURIComponent(gid)}/members`),
  addMembers: (gid: string, user_ids: string[]) =>
    idpHttp<{ added: string[] }>(`/v1/orgs/${encodeURIComponent(gid)}/members`, { method: 'POST', body: { user_ids } }),
  removeMember: (gid: string, userId: string) =>
    idpHttp<{ removed: boolean }>(`/v1/orgs/${encodeURIComponent(gid)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
};

/** 无组织保留组的 group_id（与后端 NO_ORG_GROUP_ID 一致）。 */
export const NO_ORG_GROUP_ID = '__none__';

export const UserApi = {
  list: (page = 1, page_size = 200) => idpHttp<IamPaged<IamUser>>('/v1/users/', { query: { page, page_size } }),
  get: (id: string) => idpHttp<IamUser>(`/v1/users/${encodeURIComponent(id)}`),
  create: (body: { user_id?: string; display_name: string; is_admin?: boolean; username: string; password: string }) =>
    idpHttp<IamUser>('/v1/users/', { method: 'POST', body }),
  update: (id: string, body: { display_name?: string; is_admin?: boolean; status?: string; password?: string }) =>
    idpHttp<IamUser>(`/v1/users/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) => idpHttp<{ deleted: boolean }>(`/v1/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  setOrgs: (id: string, group_ids: string[]) =>
    idpHttp<{ group_ids: string[] }>(`/v1/users/${encodeURIComponent(id)}/orgs`, { method: 'PUT', body: { group_ids } }),
  batchCreate: (
    users: Array<{ username: string; password: string; display_name?: string; is_admin?: boolean; orgs?: string[] }>,
  ) =>
    idpHttp<{
      summary: { total: number; ok: number; failed: number };
      results: Array<{ row: number; username: string; ok: boolean; user_id?: string; warnings?: string[]; error?: string }>;
    }>('/v1/users/batch', { method: 'POST', body: { users } }),
};

export const AgentTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'template_id' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<AgentTemplate>>('/v1/agent-templates/', { query: params }),
  get: (id: string) => http<AgentTemplate>(`/v1/agent-templates/${encodeURIComponent(id)}`),
  create: (body: {
    template_name: string;
    description?: string;
    agent_tags?: string[];
    template_ref?: Record<string, string[]>;
    enabled?: boolean;
    data?: Record<string, unknown> | null;
  }) => http<AgentTemplate>('/v1/agent-templates/', { method: 'POST', body }),
  update: (
    id: string,
    body: {
      template_name?: string;
      description?: string;
      agent_tags?: string[];
      template_ref?: Record<string, string[]>;
      enabled?: boolean;
      data?: Record<string, unknown> | null;
    },
  ) => http<AgentTemplate>(`/v1/agent-templates/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) =>
    http<{ deleted: boolean }>(`/v1/agent-templates/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
export const BotApi = AgentTemplateApi;

// 某实例已实例化的 Agent（目录信息 + 该实例上的 instance_agent_resource）。
export interface InstanceAgentResource extends AgentTemplate {
  resource_id: string;
  resource_name?: string | null;
  resource_desc?: string | null;
  /** 该 resource_id 下的 instance_agent_resource 行 */
  records: InstanceAgentResourceRecord[];
}

/** 实例 Agent 资源（instance_agent_resource，admin）。 */
export const InstanceAgentResourceApi = {
  listInstanceAgentResources: (
    jid: string,
    params?: {
      page?: number;
      page_size?: number;
      search?: string;
      enabled?: boolean;
      sort_by?: 'resource_id' | 'template_name' | 'granted_by' | 'expires_at' | 'enabled' | 'updated_at';
      sort_order?: 'asc' | 'desc';
    },
  ) =>
    http<PageResult<InstanceAgentResource>>(
      `/v1/instances/${encodeURIComponent(jid)}/agent-resources`,
      { query: params },
    ),
  create: (
    jid: string,
    body: {
      ref_template_id: string;
      match_exprs: MatchExpr[];
      resource_name: string;
      resource_desc?: string | null;
      enabled?: boolean;
      expires_at?: string | null;
    },
  ) =>
    http<{ items: InstanceAgentResourceRecord[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/agent-resources`,
      { method: 'POST', body },
    ),
  update: (
    jid: string,
    resourceId: string,
    body: {
      match_exprs: MatchExpr[];
      resource_name: string;
      resource_desc?: string | null;
      enabled?: boolean;
      expires_at?: string | null;
    },
  ) =>
    http<{ items: InstanceAgentResourceRecord[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/agent-resources/${encodeURIComponent(resourceId)}`,
      { method: 'PATCH', body },
    ),
  remove: (jid: string, resourceId: string) =>
    http<{ removed: boolean }>(
      `/v1/instances/${encodeURIComponent(jid)}/agent-resources/${encodeURIComponent(resourceId)}`,
      { method: 'DELETE' },
    ),
};

/** instance_service_resource 表一行（授权即实例化）。 */
export interface InstanceServiceResourceRecord {
  id: number;
  jiuwenclaw_id: string;
  resource_id: string;
  resource_name: string;
  resource_desc: string | null;
  ref_template_id: string;
  match_expr: MatchExpr;
  priority: number;
  granted_by: string | null;
  expires_at: string | null;
  enabled: boolean;
  data: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

/** 某实例已授权的服务资源（模板信息 + records）。 */
export interface InstanceServiceResource {
  id: number;
  template_id: string;
  template_name: string;
  description: string | null;
  enabled: boolean;
  resource_id: string;
  resource_name: string | null;
  resource_desc: string | null;
  ref_template_id: string;
  records: InstanceServiceResourceRecord[];
}

/** 实例服务资源（instance_service_resource，admin）。 */
export const InstanceServiceResourceApi = {
  listInstanceResources: (
    jid: string,
    params?: {
      page?: number;
      page_size?: number;
      search?: string;
      enabled?: boolean;
      sort_by?:
        | 'resource_id'
        | 'resource_name'
        | 'template_name'
        | 'priority'
        | 'granted_by'
        | 'expires_at'
        | 'enabled'
        | 'updated_at';
      sort_order?: 'asc' | 'desc';
    },
  ) =>
    http<PageResult<InstanceServiceResource>>(
      `/v1/instances/${encodeURIComponent(jid)}/service-resources`,
      { query: params },
    ),
  create: (
    jid: string,
    body: {
      ref_template_id: string;
      match_exprs: MatchExpr[];
      resource_name: string;
      resource_desc?: string | null;
      priority?: number;
      enabled?: boolean;
      expires_at?: string | null;
    },
  ) =>
    http<{ items: InstanceServiceResourceRecord[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/service-resources`,
      { method: 'POST', body },
    ),
  update: (
    jid: string,
    resourceId: string,
    body: {
      match_exprs: MatchExpr[];
      resource_name: string;
      resource_desc?: string | null;
      priority?: number;
      enabled?: boolean;
      expires_at?: string | null;
    },
  ) =>
    http<{ items: InstanceServiceResourceRecord[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/service-resources/${encodeURIComponent(resourceId)}`,
      { method: 'PATCH', body },
    ),
  remove: (jid: string, resourceId: string) =>
    http<{ removed: boolean }>(
      `/v1/instances/${encodeURIComponent(jid)}/service-resources/${encodeURIComponent(resourceId)}`,
      { method: 'DELETE' },
    ),
};

/** 实例准入绑定 instance_grant 行。 */
export type LoginPolicy = 'allow' | 'deny';

export interface InstanceGrant {
  id: number;
  jiuwenclaw_id: string;
  subject_type: 'user' | 'org' | string;
  subject_id: string;
  granted_by: string | null;
  login_policy: LoginPolicy | string;
  expires_at: string | null;
  enabled: boolean;
  data: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export type InstanceBindOptions = {
  login_policy?: LoginPolicy;
  expires_at?: string | null;
  enabled?: boolean;
};

/** 实例(gateway) ↔ 用户/组织 绑定（instance_grant；全部走管理 API /api，admin）。 */
export const InstanceBindingApi = {
  // 用户 ↔ 实例
  listUsers: (jid: string) =>
    http<{ items: InstanceGrant[]; user_ids: string[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/users`,
    ),
  bindUsers: (jid: string, ids: string[], options?: InstanceBindOptions) =>
    http<{ added: string[]; skipped: string[] }>(`/v1/instances/${encodeURIComponent(jid)}/users`, {
      method: 'POST',
      body: {
        ids,
        login_policy: options?.login_policy ?? 'allow',
        expires_at: options?.expires_at ?? null,
        enabled: options?.enabled ?? true,
      },
    }),
  updateUserGrant: (
    jid: string,
    userId: string,
    body: {
      enabled?: boolean;
      login_policy?: LoginPolicy;
      expires_at?: string | null;
      clear_expires_at?: boolean;
    },
  ) =>
    http<InstanceGrant>(
      `/v1/instances/${encodeURIComponent(jid)}/users/${encodeURIComponent(userId)}`,
      { method: 'PATCH', body },
    ),
  unbindUsers: (jid: string, ids: string[]) =>
    http<{ removed: string[] }>(`/v1/instances/${encodeURIComponent(jid)}/users`, {
      method: 'DELETE',
      body: { ids },
    }),
  // 组织 ↔ 实例
  listOrgs: (jid: string) =>
    http<{ items: InstanceGrant[]; group_ids: string[] }>(
      `/v1/instances/${encodeURIComponent(jid)}/orgs`,
    ),
  bindOrgs: (jid: string, ids: string[], options?: InstanceBindOptions) =>
    http<{ added: string[]; skipped: string[] }>(`/v1/instances/${encodeURIComponent(jid)}/orgs`, {
      method: 'POST',
      body: {
        ids,
        login_policy: options?.login_policy ?? 'allow',
        expires_at: options?.expires_at ?? null,
        enabled: options?.enabled ?? true,
      },
    }),
  updateOrgGrant: (
    jid: string,
    groupId: string,
    body: {
      enabled?: boolean;
      login_policy?: LoginPolicy;
      expires_at?: string | null;
      clear_expires_at?: boolean;
    },
  ) =>
    http<InstanceGrant>(
      `/v1/instances/${encodeURIComponent(jid)}/orgs/${encodeURIComponent(groupId)}`,
      { method: 'PATCH', body },
    ),
  unbindOrgs: (jid: string, ids: string[]) =>
    http<{ removed: string[] }>(`/v1/instances/${encodeURIComponent(jid)}/orgs`, {
      method: 'DELETE',
      body: { ids },
    }),
  // 反查：一批实体各绑了哪些实例（所属实例列，防 N+1）。逗号分隔单参数,对反代最稳。
  userGateways: (userIds: string[]) =>
    http<{ bindings: Record<string, string[]> }>('/v1/user-gateways', {
      query: { user_ids: userIds.join(',') },
    }),
  orgGateways: (groupIds: string[]) =>
    http<{ bindings: Record<string, string[]> }>('/v1/org-gateways', {
      query: { group_ids: groupIds.join(',') },
    }),
};

export interface UserGateway {
  jiuwenclaw_id: string;
  jiuwenclaw_name: string;
  gateway_status: string;
  runtime_status?: string;
  space_id?: string;
  gateway_endpoint: string | null;
}

// 当前登录用户视角：身份来自 JWT，Manager 只返回该用户获授权的组网与 Agent。
export const UserConsoleApi = {
  orgs: () => idpHttp<{ orgs: Org[] }>('/v1/auth/me/orgs'),
  gateways: () => http<{ gateways: UserGateway[] }>('/v1/user-console/gateways'),
  agents: (groupId: string, jiuwenclawId: string) =>
    http<{ agents: AgentTemplate[] }>('/v1/user-console/agents', {
      query: { group_id: groupId, jiuwenclaw_id: jiuwenclawId },
    }),
};

// ---------- Instances ----------

interface InstancePageRaw {
  items: InstanceSummary[];
  total: number;
  page: number;
  page_size: number;
}

export const InstanceApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    gateway_status?: string;
    runtime_status?: string;
    search?: string;
    sort_by?:
      | 'jiuwenclaw_name'
      | 'gateway_status'
      | 'runtime_status'
      | 'gateway_last_alive'
      | 'runtime_last_alive'
      | 'namespace'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) =>
    http<InstancePageRaw>('/v1/instances/', { query: params }),
  get: (id: string) => http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`),
  create: (body: CreateInstanceBody) => http<InstanceSummary>('/v1/instances/', { method: 'POST', body }),
  update: (id: string, body: { data?: Record<string, unknown> }) =>
    http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string, force = false) =>
    http<{ deleted: boolean }>(`/v1/instances/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      query: { force },
    }),
};

// ---------- Templates ----------

export const ModelTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    model_type?: string;
    model_provider?: string;
    search?: string;
    sort_by?:
      | 'template_name'
      | 'description'
      | 'model_provider'
      | 'model_id'
      | 'model_type'
      | 'api_base'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) =>
    http<PageResult<ModelTemplate>>('/v1/model-templates', { query: params }),
  get: (id: string) => http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`),
  create: (body: ModelTemplateCreateBody) =>
    http<ModelTemplate>('/v1/model-templates', { method: 'POST', body }),
  update: (id: string, body: ModelTemplateUpdateBody) =>
    http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(`/v1/model-templates/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
};

export const EmbeddingTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    model_provider?: string;
    search?: string;
    sort_by?:
      | 'template_name'
      | 'description'
      | 'model_provider'
      | 'model_id'
      | 'api_base'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<EmbeddingTemplate>>('/v1/embedding-templates', { query: params }),
  get: (id: string) =>
    http<EmbeddingTemplate>(`/v1/embedding-templates/${encodeURIComponent(id)}`),
  create: (body: EmbeddingTemplateCreateBody) =>
    http<EmbeddingTemplate>('/v1/embedding-templates', { method: 'POST', body }),
  update: (id: string, body: EmbeddingTemplateUpdateBody) =>
    http<EmbeddingTemplate>(`/v1/embedding-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/embedding-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),
};

export const ExtensionTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    component?: string;
    hook_type?: string;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'component' | 'hook_type' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<ExtensionConfigTemplate>>('/v1/extension-config-templates', { query: params }),
  get: (id: string) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`),
  create: (body: ExtensionConfigTemplateCreateBody) =>
    http<ExtensionConfigTemplate>('/v1/extension-config-templates', { method: 'POST', body }),
  update: (id: string, body: ExtensionConfigTemplateUpdateBody) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/extension-config-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

export const SkillWhitelistTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    skill_id?: string;
    skill_source?: string;
    search?: string;
    sort_by?:
      | 'template_name'
      | 'description'
      | 'skill_source'
      | 'skill_id'
      | 'skill_version'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<SkillWhitelistTemplate>>('/v1/skill-whitelist-templates', { query: params }),
  get: (id: string) =>
    http<SkillWhitelistTemplate>(`/v1/skill-whitelist-templates/${encodeURIComponent(id)}`),
  create: (body: SkillWhitelistTemplateCreateBody) =>
    http<SkillWhitelistTemplate>('/v1/skill-whitelist-templates', { method: 'POST', body }),
  update: (id: string, body: SkillWhitelistTemplateUpdateBody) =>
    http<SkillWhitelistTemplate>(`/v1/skill-whitelist-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/skill-whitelist-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

export const PermissionsTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<PermissionsTemplate>>('/v1/permissions-templates', { query: params }),
  get: (id: string) =>
    http<PermissionsTemplate>(`/v1/permissions-templates/${encodeURIComponent(id)}`),
  create: (body: PermissionsTemplateCreateBody) =>
    http<PermissionsTemplate>('/v1/permissions-templates', { method: 'POST', body }),
  update: (id: string, body: PermissionsTemplateUpdateBody) =>
    http<PermissionsTemplate>(`/v1/permissions-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/permissions-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

export const ServiceConfigTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'agent_image' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<ServiceConfigTemplate>>('/v1/service-config-templates', { query: params }),
  get: (id: string) =>
    http<ServiceConfigTemplate>(`/v1/service-config-templates/${encodeURIComponent(id)}`),
  create: (body: ServiceConfigTemplateCreateBody) =>
    http<ServiceConfigTemplate>('/v1/service-config-templates', { method: 'POST', body }),
  update: (id: string, body: ServiceConfigTemplateUpdateBody) =>
    http<ServiceConfigTemplate>(`/v1/service-config-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/service-config-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

// ---------- Application Config (per-instance) ----------

function instanceBase(instanceId: string) {
  return `/v1/instances/${encodeURIComponent(instanceId)}`;
}

export const LogMaskingRuleApi = {
  list: (
    instanceId: string,
    params?: {
      enabled?: boolean;
      source?: string;
      search?: string;
      sort_by?: 'rule_name' | 'description' | 'pattern' | 'replacement' | 'priority' | 'updated_at';
      sort_order?: 'asc' | 'desc';
    }
  ) =>
    http<ListItemsResult<LogMaskingRule>>(`${instanceBase(instanceId)}/log-masking-rules`, {
      query: params,
    }),
  get: (instanceId: string, ruleId: string) =>
    http<LogMaskingRule>(
      `${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`
    ),
  create: (instanceId: string, body: LogMaskingRuleCreateBody) =>
    http<LogMaskingRule>(`${instanceBase(instanceId)}/log-masking-rules`, { method: 'POST', body }),
  update: (instanceId: string, ruleId: string, body: LogMaskingRuleUpdateBody) =>
    http<LogMaskingRule>(
      `${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, ruleId: string) =>
    http<void>(`${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),
};

export const LoggingApi = {
  get: (instanceId: string) => http<LoggingConfig>(`${instanceBase(instanceId)}/logging`),
  upsert: (instanceId: string, body: LoggingConfigUpsertBody) =>
    http<LoggingConfig>(`${instanceBase(instanceId)}/logging`, { method: 'PUT', body }),
  remove: (instanceId: string) =>
    http<void>(`${instanceBase(instanceId)}/logging`, { method: 'DELETE' }),
};
