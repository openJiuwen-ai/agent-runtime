/**
 * 服务配置模板 ↔ config_sync rawdata 互转。
 * 导入导出只处理 `{ containers, templates, scopes }`；
 * `type` / `metadata` 由 Manager 下发 Runtime 时自行拼接。
 * 导入兼容误带 Envelope 的文件（自动取 rawdata）。
 */
import type {
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
 * Manager 模板 → Runtime wire 模板。
 * 对齐联调样例：仅写 nodeName（勿双写 node_name，Runtime 会拒收）。
 */
export function templateToWire(
  row: ServiceConfigTemplate,
  options?: { templateId?: string; splitForm?: boolean },
): Record<string, unknown> {
  const split = options?.splitForm ?? Boolean(row.main_container_id);
  const wire: Record<string, unknown> = {
    template_id: options?.templateId || row.template_id,
    template_name: row.template_name,
    pod_name: row.pod_name || 'agentserver',
    namespace: row.namespace || 'default',
    sse_path: row.sse_path || '/sse',
    scope_concurrency: row.session_concurrency,
    pod_concurrency: row.service_concurrency,
    session_ttl: row.session_ttl,
    pod_ttl: row.service_ttl,
    min_idle_pods: row.min_idle_services,
    ready_timeout: row.ready_timeout,
  };
  if (row.description) wire.description = row.description;
  if (row.node_name) wire.nodeName = row.node_name;
  if (row.kubeconfig) wire.kubeconfig = row.kubeconfig;
  // 样例未写这些字段；仅非默认时带上，避免噪音
  if (row.ready_poll_interval != null && row.ready_poll_interval !== 2) {
    wire.ready_poll_interval = row.ready_poll_interval;
  }
  if (row.message_timeout != null && row.message_timeout !== 600) {
    wire.message_timeout = row.message_timeout;
  }
  if (row.enabled === false) wire.enabled = false;

  if (split) {
    if (row.main_container_id) wire.main_container_id = row.main_container_id;
    if (row.sidecar_container_ids?.length) {
      wire.sidecar_container_ids = [...row.sidecar_container_ids];
    }
    if (row.volumes?.length) wire.volumes = row.volumes;
    return wire;
  }

  wire.agent_image = row.agent_image ?? '';
  wire.container_name = row.container_name;
  wire.container_port = row.container_port;
  wire.port_name = row.port_name;
  wire.sse_port = row.sse_port;
  wire.health_path = row.health_path;
  wire.image_pull_policy = row.image_pull_policy;
  if (row.agent_env) wire.agent_env = row.agent_env;
  if (row.run_as_user != null) wire.run_as_user = row.run_as_user;
  if (row.run_as_group != null) wire.run_as_group = row.run_as_group;
  if (row.readiness_initial_delay != null) {
    wire.readiness_initial_delay = row.readiness_initial_delay;
  }
  if (row.readiness_period != null) wire.readiness_period = row.readiness_period;
  if (row.nfs_server) wire.nfs_server = row.nfs_server;
  if (row.nfs_path) wire.nfs_path = row.nfs_path;
  if (row.nfs_mount_path) wire.nfs_mount_path = row.nfs_mount_path;
  if (row.agent_cpu_request) wire.agent_cpu_request = row.agent_cpu_request;
  if (row.agent_memory_request) wire.agent_memory_request = row.agent_memory_request;
  if (row.agent_cpu_limit) wire.agent_cpu_limit = row.agent_cpu_limit;
  if (row.agent_memory_limit) wire.agent_memory_limit = row.agent_memory_limit;
  if (row.sidecars?.length) wire.sidecars = row.sidecars;
  if (row.agent_host_path_mounts?.length) {
    wire.agent_host_path_mounts = row.agent_host_path_mounts;
  }
  if (row.agent_configmap_mounts?.length) {
    wire.agent_configmap_mounts = row.agent_configmap_mounts;
  }
  if (row.agent_pvc_mounts?.length) wire.agent_pvc_mounts = row.agent_pvc_mounts;
  return wire;
}

function synthesizeMainContainer(row: ServiceConfigTemplate): Record<string, unknown> {
  const cid = row.main_container_id || `c-${row.template_id}-main`;
  const container: Record<string, unknown> = {
    container_id: cid,
    name: row.container_name || 'agent',
    image: row.agent_image || '',
    imagePullPolicy: row.image_pull_policy || 'IfNotPresent',
    ports: [
      {
        name: row.port_name || 'http',
        containerPort: row.container_port || 8080,
      },
    ],
    env: envMapToList(row.agent_env),
  };
  if (row.run_as_user != null || row.run_as_group != null) {
    container.securityContext = {
      ...(row.run_as_user != null ? { runAsUser: row.run_as_user } : {}),
      ...(row.run_as_group != null ? { runAsGroup: row.run_as_group } : {}),
    };
  }
  if (row.health_path || row.readiness_initial_delay != null) {
    container.readinessProbe = {
      httpGet: {
        path: row.health_path || '/health',
        port: row.sse_port || row.container_port || 8080,
      },
      initialDelaySeconds: row.readiness_initial_delay ?? 5,
      periodSeconds: row.readiness_period ?? 5,
    };
  }
  const resources: Record<string, unknown> = {};
  const requests: Record<string, string> = {};
  const limits: Record<string, string> = {};
  if (row.agent_cpu_request) requests.cpu = row.agent_cpu_request;
  if (row.agent_memory_request) requests.memory = row.agent_memory_request;
  if (row.agent_cpu_limit) limits.cpu = row.agent_cpu_limit;
  if (row.agent_memory_limit) limits.memory = row.agent_memory_limit;
  if (Object.keys(requests).length) resources.requests = requests;
  if (Object.keys(limits).length) resources.limits = limits;
  if (Object.keys(resources).length) container.resources = resources;
  return container;
}

/** 导出 rawdata（三段式）。 */
export function exportTemplateRawdata(row: ServiceConfigTemplate): ConfigSyncRawdata {
  const stored = pickStoredSync(row.data ?? undefined);
  const hasStoredContainers = (stored.containers?.length ?? 0) > 0;
  const splitForm = hasStoredContainers || Boolean(row.main_container_id);
  const wire = templateToWire(row, {
    templateId: stored.source_template_id || row.template_id,
    splitForm,
  });

  let containers = stored.containers ?? [];
  if (!containers.length && (row.main_container_id || row.agent_image)) {
    const main = synthesizeMainContainer(row);
    containers = [main];
    if (!wire.main_container_id) {
      wire.main_container_id = String(main.container_id);
    }
  }

  return {
    containers,
    templates: [wire],
    scopes: stored.scopes ?? [],
  };
}

function hydrateFromContainer(
  body: ServiceConfigTemplateCreateBody,
  container: Record<string, unknown>,
): void {
  if (!body.agent_image && container.image != null) {
    body.agent_image = String(container.image);
  }
  if (!body.container_name && container.name != null) {
    body.container_name = String(container.name);
  }
  const pull = container.imagePullPolicy ?? container.image_pull_policy;
  if (!body.image_pull_policy && pull != null) {
    body.image_pull_policy = String(pull);
  }
  const ports = asArray(container.ports);
  const firstPort = asRecord(ports[0]);
  if (firstPort) {
    const port = optInt(firstPort.containerPort ?? firstPort.container_port);
    if (port != null && body.container_port == null) body.container_port = port;
    const pname = optStr(firstPort.name);
    if (pname && !body.port_name) body.port_name = pname;
  }
  const env = envListToMap(container.env);
  if (env && !body.agent_env) body.agent_env = env;
  const sc = asRecord(container.securityContext ?? container.security_context);
  if (sc) {
    if (body.run_as_user == null) {
      body.run_as_user = optInt(sc.runAsUser ?? sc.run_as_user) ?? null;
    }
    if (body.run_as_group == null) {
      body.run_as_group = optInt(sc.runAsGroup ?? sc.run_as_group) ?? null;
    }
  }
  const probe = asRecord(container.readinessProbe ?? container.readiness_probe);
  if (probe) {
    if (body.readiness_initial_delay == null) {
      body.readiness_initial_delay = optInt(
        probe.initialDelaySeconds ?? probe.initial_delay_seconds,
      );
    }
    if (body.readiness_period == null) {
      body.readiness_period = optInt(probe.periodSeconds ?? probe.period_seconds);
    }
    const httpGet = asRecord(probe.httpGet ?? probe.http_get);
    if (httpGet?.path != null && !body.health_path) {
      body.health_path = String(httpGet.path);
    }
    const probePort = optInt(httpGet?.port);
    if (probePort != null && body.sse_port == null) body.sse_port = probePort;
  }
  const resources = asRecord(container.resources);
  const requests = asRecord(resources?.requests);
  const limits = asRecord(resources?.limits);
  if (requests?.cpu != null && !body.agent_cpu_request) {
    body.agent_cpu_request = String(requests.cpu);
  }
  if (requests?.memory != null && !body.agent_memory_request) {
    body.agent_memory_request = String(requests.memory);
  }
  if (limits?.cpu != null && !body.agent_cpu_limit) {
    body.agent_cpu_limit = String(limits.cpu);
  }
  if (limits?.memory != null && !body.agent_memory_limit) {
    body.agent_memory_limit = String(limits.memory);
  }
}

/** Runtime wire / 内联模板 → Manager CreateBody。 */
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
    agent_image: optStr(wire.agent_image) ?? '',
    namespace: optStr(wire.namespace) || 'default',
    node_name: nodeName,
    run_as_user: optInt(wire.run_as_user) ?? null,
    run_as_group: optInt(wire.run_as_group) ?? null,
    pod_name: optStr(wire.pod_name) || 'agentserver',
    container_name: optStr(wire.container_name) || 'agent',
    container_port: optInt(wire.container_port) ?? 8080,
    port_name: optStr(wire.port_name) || 'http',
    sse_port: optInt(wire.sse_port) ?? optInt(wire.container_port) ?? 8080,
    sse_path: optStr(wire.sse_path) || '/sse',
    health_path: optStr(wire.health_path) || '/health',
    agent_env: (() => {
      const fromList = envListToMap(wire.agent_env);
      if (fromList) return fromList;
      const rec = asRecord(wire.agent_env);
      if (!rec) return undefined;
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(rec)) {
        if (v == null) continue;
        out[k] = String(v);
      }
      return Object.keys(out).length ? out : undefined;
    })(),
    image_pull_policy: (optStr(wire.image_pull_policy) || 'IfNotPresent') as
      | 'Always'
      | 'IfNotPresent'
      | 'Never',
    kubeconfig: optStr(wire.kubeconfig),
    readiness_initial_delay: optInt(wire.readiness_initial_delay) ?? 5,
    readiness_period: optInt(wire.readiness_period) ?? 5,
    ready_timeout: optInt(wire.ready_timeout) ?? 300,
    ready_poll_interval: optInt(wire.ready_poll_interval) ?? 2,
    nfs_server: optStr(wire.nfs_server),
    nfs_path: optStr(wire.nfs_path),
    nfs_mount_path: optStr(wire.nfs_mount_path),
    agent_cpu_request: optStr(wire.agent_cpu_request),
    agent_memory_request: optStr(wire.agent_memory_request),
    agent_cpu_limit: optStr(wire.agent_cpu_limit),
    agent_memory_limit: optStr(wire.agent_memory_limit),
    sidecars: asArray(wire.sidecars).filter(asRecord) as Record<string, unknown>[],
    agent_host_path_mounts: asArray(wire.agent_host_path_mounts).filter(
      asRecord,
    ) as Record<string, unknown>[],
    agent_configmap_mounts: asArray(wire.agent_configmap_mounts).filter(
      asRecord,
    ) as Record<string, unknown>[],
    agent_pvc_mounts: asArray(wire.agent_pvc_mounts).filter(asRecord) as Record<
      string,
      unknown
    >[],
    main_container_id: optStr(wire.main_container_id),
    sidecar_container_ids: asArray(wire.sidecar_container_ids)
      .map((id) => String(id).trim())
      .filter(Boolean),
    volumes: asArray(wire.volumes).filter(asRecord) as Record<string, unknown>[],
    min_idle_services: optInt(wire.min_idle_pods ?? wire.min_idle_services) ?? 0,
    service_concurrency:
      optInt(wire.pod_concurrency ?? wire.service_concurrency) ?? 2,
    service_ttl: optInt(wire.pod_ttl ?? wire.service_ttl) ?? 300,
    message_timeout: optInt(wire.message_timeout) ?? 600,
    session_concurrency:
      optInt(wire.scope_concurrency ?? wire.session_concurrency) ?? 3,
    session_ttl: optInt(wire.session_ttl) ?? 60,
    enabled: wire.enabled === false ? false : true,
  };

  const byId = new Map<string, Record<string, unknown>>();
  for (const c of containers) {
    const id = optStr(c.container_id);
    if (id) byId.set(id, c);
  }
  if (body.main_container_id && byId.has(body.main_container_id)) {
    hydrateFromContainer(body, byId.get(body.main_container_id)!);
  } else if (!body.main_container_id && containers.length === 1) {
    const only = containers[0];
    const cid = optStr(only.container_id);
    if (cid) body.main_container_id = cid;
    hydrateFromContainer(body, only);
  }

  if (!body.sidecars?.length) delete body.sidecars;
  if (!body.agent_host_path_mounts?.length) delete body.agent_host_path_mounts;
  if (!body.agent_configmap_mounts?.length) delete body.agent_configmap_mounts;
  if (!body.agent_pvc_mounts?.length) delete body.agent_pvc_mounts;
  if (!body.sidecar_container_ids?.length) delete body.sidecar_container_ids;
  if (!body.volumes?.length) delete body.volumes;
  if (!body.agent_env || !Object.keys(body.agent_env).length) delete body.agent_env;

  const data: Record<string, unknown> = {};
  const configSync: StoredSync = {};
  if (containers.length) configSync.containers = containers;
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
    root.main_container_id
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
