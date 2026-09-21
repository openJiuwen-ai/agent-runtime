/**
 * 服务配置模板 ↔ config_sync rawdata 互转。
 * 导入导出只处理 `{ containers, templates, scopes }`；
 * `type` / `metadata` 由 Manager 下发 Runtime 时自行拼接。
 * 导入兼容误带 Envelope 的文件（自动取 rawdata）。
 * 导出强制 split：template 仅带 refs，容器规格从容器模板目录按绑定解析
 * （Out.data 不再内联 containers；解析不到时回退存量 data 内联，旧文件过渡）。
 * 导入兼容旧 inline wire：前端合成 containers 写入 data.config_sync。
 */
import { ContainerTemplateApi } from '../services/api';
import type {
  ContainerTemplate,
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
} from '../types';

export type ConfigSyncRawdata = {
  containers: Record<string, unknown>[];
  templates: Record<string, unknown>[];
  scopes: Record<string, unknown>[];
};

type StoredSync = {
  containers?: Record<string, unknown>[];
  scopes?: Record<string, unknown>[];
  /** 导入时原始 template_id，导出时优先写回以对齐 scopes.template_id。 */
  source_template_id?: string;
};

function asRecord(v: unknown): Record<string, unknown> | null {
  return v != null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function optStr(v: unknown): string | undefined {
  if (v == null) return undefined;
  const s = String(v).trim();
  return s || undefined;
}

function optInt(v: unknown): number | undefined {
  if (v == null || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : undefined;
}

function envListToMap(env: unknown): Record<string, string> | undefined {
  if (!Array.isArray(env)) {
    if (env && typeof env === 'object' && !Array.isArray(env)) {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(env as Record<string, unknown>)) {
        if (v == null) continue;
        out[k] = String(v);
      }
      return Object.keys(out).length ? out : undefined;
    }
    return undefined;
  }
  const out: Record<string, string> = {};
  for (const item of env) {
    const row = asRecord(item);
    if (!row || row.name == null) continue;
    out[String(row.name)] = row.value == null ? '' : String(row.value);
  }
  return Object.keys(out).length ? out : undefined;
}

function envMapToList(
  env: Record<string, string> | null | undefined,
): { name: string; value: string }[] {
  if (!env) return [];
  return Object.entries(env).map(([name, value]) => ({ name, value: String(value) }));
}

function pickStoredSync(data: Record<string, unknown> | null | undefined): StoredSync {
  const sync = asRecord(data?.config_sync) ?? asRecord(data);
  if (!sync) return {};
  return {
    containers: asArray(sync.containers).filter(asRecord) as Record<string, unknown>[],
    scopes: asArray(sync.scopes).filter(asRecord) as Record<string, unknown>[],
    source_template_id: optStr(sync.source_template_id),
  };
}

/**
 * Manager 模板 → Runtime wire 模板（强制 split，不写 inline 容器键）。
 * 对齐联调样例：仅写 nodeName（勿双写 node_name，Runtime 会拒收）。
 */
export function templateToWire(
  row: ServiceConfigTemplate,
  options?: { templateId?: string },
): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    template_id: options?.templateId || row.template_id,
    template_name: row.template_name,
    pod_name: row.pod_name || 'agentserver',
    // ns 不可配：空串 = 继承，AgentServer 跟随 runtime 自身 ns
    namespace: '',
    sse_path: row.sse_path || '/sse',
    scope_concurrency: row.scope_concurrency,
    pod_concurrency: row.pod_concurrency,
    session_ttl: row.session_ttl,
    pod_ttl: row.pod_ttl,
    min_idle_pods: row.min_idle_pods,
    ready_timeout: row.ready_timeout,
  };
  if (row.description) wire.description = row.description;
  if (row.node_name) wire.nodeName = row.node_name;
  if (row.fs_group != null) wire.fsGroup = row.fs_group;
  if (row.kubeconfig) wire.kubeconfig = row.kubeconfig;
  // 样例未写这些字段；仅非默认时带上，避免噪音
  if (row.ready_poll_interval != null && row.ready_poll_interval !== 2) {
    wire.ready_poll_interval = row.ready_poll_interval;
  }
  if (row.message_timeout != null && row.message_timeout !== 600) {
    wire.message_timeout = row.message_timeout;
  }
  if (row.enabled === false) wire.enabled = false;

  if (row.main_container_id) wire.main_container_id = row.main_container_id;
  if (row.sidecar_container_ids?.length) {
    wire.sidecar_container_ids = [...row.sidecar_container_ids];
  }
  if (row.volumes?.length) wire.volumes = row.volumes;
  return wire;
}

/**
 * 从旧版 inline wire 字段合成主容器规格（导入兼容）。
 * CreateBody 不再写这些已删列，合成结果放入 data.config_sync.containers。
 */
function synthesizeMainContainerFromWire(
  wire: Record<string, unknown>,
): Record<string, unknown> {
  const tid = optStr(wire.template_id) || 'imported';
  const cid = optStr(wire.main_container_id) || `c-${tid}-main`;
  const containerPort = optInt(wire.container_port) ?? 8080;
  const ssePort = optInt(wire.sse_port) ?? containerPort;
  const agentEnv =
    envListToMap(wire.agent_env) ??
    (() => {
      const rec = asRecord(wire.agent_env);
      if (!rec) return undefined;
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(rec)) {
        if (v == null) continue;
        out[k] = String(v);
      }
      return Object.keys(out).length ? out : undefined;
    })();

  const container: Record<string, unknown> = {
    container_id: cid,
    name: optStr(wire.container_name) || 'agent',
    image: optStr(wire.agent_image) || '',
    imagePullPolicy: optStr(wire.image_pull_policy) || 'IfNotPresent',
    ports: [
      {
        name: optStr(wire.port_name) || 'http',
        containerPort,
      },
    ],
    env: envMapToList(agentEnv),
  };

  const runAsUser = optInt(wire.run_as_user);
  const runAsGroup = optInt(wire.run_as_group);
  if (runAsUser != null || runAsGroup != null) {
    container.securityContext = {
      ...(runAsUser != null ? { runAsUser } : {}),
      ...(runAsGroup != null ? { runAsGroup } : {}),
    };
  }

  const healthPath = optStr(wire.health_path);
  const readinessInitial = optInt(wire.readiness_initial_delay);
  if (healthPath || readinessInitial != null) {
    container.readinessProbe = {
      httpGet: {
        path: healthPath || '/health',
        port: ssePort,
      },
      initialDelaySeconds: readinessInitial ?? 5,
      periodSeconds: optInt(wire.readiness_period) ?? 5,
    };
  }

  const resources: Record<string, unknown> = {};
  const requests: Record<string, string> = {};
  const limits: Record<string, string> = {};
  if (optStr(wire.agent_cpu_request)) requests.cpu = String(wire.agent_cpu_request);
  if (optStr(wire.agent_memory_request)) {
    requests.memory = String(wire.agent_memory_request);
  }
  if (optStr(wire.agent_cpu_limit)) limits.cpu = String(wire.agent_cpu_limit);
  if (optStr(wire.agent_memory_limit)) limits.memory = String(wire.agent_memory_limit);
  if (Object.keys(requests).length) resources.requests = requests;
  if (Object.keys(limits).length) resources.limits = limits;
  if (Object.keys(resources).length) container.resources = resources;

  return container;
}

function looksLikeInlineWire(wire: Record<string, unknown>): boolean {
  return Boolean(
    optStr(wire.agent_image) ||
      optStr(wire.container_name) ||
      wire.container_port != null ||
      wire.agent_env != null ||
      wire.health_path != null,
  );
}

/** 容器模板行 → config_sync wire 容器（对齐后端 row_to_wire 键形）。 */
function containerTemplateToWire(tpl: ContainerTemplate): Record<string, unknown> {
  const wire: Record<string, unknown> = {
    container_id: tpl.container_id,
    name: tpl.name,
    image: tpl.image,
    imagePullPolicy: tpl.image_pull_policy || 'IfNotPresent',
  };
  for (const [wireKey, value] of [
    ['ports', tpl.ports],
    ['env', tpl.env],
    ['envFrom', tpl.env_from],
    ['resources', tpl.resources],
    ['volumeMounts', tpl.volume_mounts],
    ['securityContext', tpl.security_context],
    ['readinessProbe', tpl.readiness_probe],
  ] as const) {
    if (value != null) wire[wireKey] = value;
  }
  return wire;
}

/**
 * 导出 rawdata（三段式，强制 split）。
 * 绑定的容器规格优先从容器模板目录按 container_id 解析；
 * 目录缺失该绑定（存量数据/被删）时回退 Out.data 内联 containers。
 */
export async function exportTemplateRawdata(
  row: ServiceConfigTemplate,
  catalog?: ContainerTemplate[],
): Promise<ConfigSyncRawdata> {
  const stored = pickStoredSync(row.data ?? undefined);
  const wire = templateToWire(row, {
    templateId: stored.source_template_id || row.template_id,
  });

  const boundIds = [
    ...new Set(
      [row.main_container_id, ...(row.sidecar_container_ids ?? [])]
        .filter((id): id is string => Boolean(id && id.trim())),
    ),
  ];
  const containers: Record<string, unknown>[] = [];
  const missingIds: string[] = [];
  if (boundIds.length) {
    let catalogRows = catalog;
    if (!catalogRows) {
      try {
        catalogRows = (await ContainerTemplateApi.list({ page: 1, page_size: 200 })).items ?? [];
      } catch {
        catalogRows = [];
      }
    }
    const byId = new Map(catalogRows.map((tpl) => [tpl.container_id, tpl]));
    for (const cid of boundIds) {
      const tpl = byId.get(cid);
      if (tpl) {
        containers.push(containerTemplateToWire(tpl));
      } else {
        missingIds.push(cid);
      }
    }
  }
  // 存量兜底：内联数据里还有目录解析不到的绑定
  if (missingIds.length) {
    const inlineById = new Map(
      stored.containers
        ?.filter((c) => typeof c.container_id === 'string')
        .map((c) => [String(c.container_id), c]) ?? [],
    );
    for (const cid of missingIds) {
      const inline = inlineById.get(cid);
      if (inline) containers.push(inline);
    }
  }

  return {
    containers,
    templates: [wire],
    scopes: stored.scopes ?? [],
  };
}

/** Runtime wire / 内联模板 → Manager CreateBody（仅保留字段 + data.config_sync）。 */
export function wireTemplateToCreateBody(
  wire: Record<string, unknown>,
  containers: Record<string, unknown>[] = [],
  options?: {
    scopes?: Record<string, unknown>[];
  },
): ServiceConfigTemplateCreateBody {
  const nodeName = optStr(wire.nodeName ?? wire.node_name);
  const body: ServiceConfigTemplateCreateBody = {
    template_name:
      optStr(wire.template_name) ||
      optStr(wire.template_id) ||
      'imported-template',
    description: optStr(wire.description),
    node_name: nodeName,
    fs_group: optInt(wire.fsGroup ?? wire.fs_group) ?? null,
    pod_name: optStr(wire.pod_name) || 'agentserver',
    sse_path: optStr(wire.sse_path) || '/sse',
    kubeconfig: optStr(wire.kubeconfig),
    ready_timeout: optInt(wire.ready_timeout) ?? 300,
    ready_poll_interval: optInt(wire.ready_poll_interval) ?? 2,
    main_container_id: optStr(wire.main_container_id),
    sidecar_container_ids: asArray(wire.sidecar_container_ids)
      .map((id) => String(id).trim())
      .filter(Boolean),
    volumes: asArray(wire.volumes).filter(asRecord) as Record<string, unknown>[],
    min_idle_pods: optInt(wire.min_idle_pods ?? wire.min_idle_services) ?? 0,
    pod_concurrency: optInt(wire.pod_concurrency ?? wire.service_concurrency) ?? 2,
    pod_ttl: optInt(wire.pod_ttl ?? wire.service_ttl) ?? 300,
    message_timeout: optInt(wire.message_timeout) ?? 600,
    scope_concurrency:
      optInt(wire.scope_concurrency ?? wire.session_concurrency) ?? 3,
    session_ttl: optInt(wire.session_ttl) ?? 60,
    enabled: wire.enabled === false ? false : true,
  };

  let resolvedContainers = [...containers];

  // 旧 inline wire：无 containers 时从前端合成，不写已删模板列
  if (!resolvedContainers.length && looksLikeInlineWire(wire)) {
    const main = synthesizeMainContainerFromWire(wire);
    resolvedContainers = [main];
    if (!body.main_container_id) {
      body.main_container_id = String(main.container_id);
    }
    const inlineSidecars = asArray(wire.sidecars).filter(asRecord) as Record<
      string,
      unknown
    >[];
    if (inlineSidecars.length) {
      const sideIds: string[] = [];
      for (let i = 0; i < inlineSidecars.length; i++) {
        const side = { ...inlineSidecars[i] };
        const sid =
          optStr(side.container_id) ||
          `c-${optStr(wire.template_id) || 'imported'}-side-${i}`;
        side.container_id = sid;
        resolvedContainers.push(side);
        sideIds.push(sid);
      }
      if (!body.sidecar_container_ids?.length) {
        body.sidecar_container_ids = sideIds;
      }
    }
  } else if (!body.main_container_id && resolvedContainers.length === 1) {
    const cid = optStr(resolvedContainers[0].container_id);
    if (cid) body.main_container_id = cid;
  }

  if (!body.sidecar_container_ids?.length) delete body.sidecar_container_ids;
  if (!body.volumes?.length) delete body.volumes;

  const data: Record<string, unknown> = {};
  const configSync: StoredSync = {};
  if (resolvedContainers.length) configSync.containers = resolvedContainers;
  if (options?.scopes?.length) configSync.scopes = options.scopes;
  const sourceTid = optStr(wire.template_id);
  if (sourceTid) configSync.source_template_id = sourceTid;
  if (Object.keys(configSync).length) data.config_sync = configSync;
  if (Object.keys(data).length) body.data = data;

  return body;
}

export function parseConfigSyncImport(parsed: unknown): {
  body: ServiceConfigTemplateCreateBody;
  templateCount: number;
} {
  const root = asRecord(parsed);
  if (!root) {
    throw new Error('JSON root must be an object');
  }

  let rawdata: Record<string, unknown> | null = null;

  // 兼容旧联调文件带 Envelope：只取 rawdata，忽略 type/metadata
  if (root.rawdata != null) {
    rawdata = asRecord(root.rawdata);
    if (!rawdata) throw new Error('rawdata must be an object');
  } else if (
    Array.isArray(root.templates) ||
    Array.isArray(root.containers) ||
    Array.isArray(root.scopes)
  ) {
    rawdata = root;
  } else if (
    optStr(root.template_name) ||
    optStr(root.template_id) ||
    root.main_container_id ||
    looksLikeInlineWire(root)
  ) {
    return {
      body: wireTemplateToCreateBody(root, []),
      templateCount: 1,
    };
  } else {
    throw new Error(
      'unsupported JSON: expect {containers,templates,scopes}, or a template object',
    );
  }

  const containers = asArray(rawdata.containers).filter(asRecord) as Record<
    string,
    unknown
  >[];
  const templates = asArray(rawdata.templates).filter(asRecord) as Record<
    string,
    unknown
  >[];
  const scopes = asArray(rawdata.scopes).filter(asRecord) as Record<
    string,
    unknown
  >[];

  if (!templates.length) {
    throw new Error('templates is empty');
  }

  return {
    body: wireTemplateToCreateBody(templates[0], containers, { scopes }),
    templateCount: templates.length,
  };
}

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function exportFilename(row: ServiceConfigTemplate): string {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const safe = (row.template_name || row.template_id || 'template')
    .replace(/[^\w.-]+/g, '_')
    .slice(0, 48);
  return `${stamp}${safe}.json`;
}
