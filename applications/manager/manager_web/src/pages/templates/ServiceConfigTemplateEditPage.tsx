/**
 * 服务配置模板编辑页（三段式布局对齐 HLD）：
 * 1) 模板级：namespace/nodeName/pod/volumes/池策略/sse_path…
 * 2) AgentServer 主容器
 * 3) Sandbox sidecar 容器
 * 保存时写入 Manager 模板列 + data.config_sync.containers（供导入导出往返）。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import { useRouter } from '../../router';
import { toast } from '../../stores/uiStore';
import { isValidK8sCpu, isValidK8sMemory } from '../../utils/k8sResource';
import { isValidUnixAbsPath } from '../../utils/path';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
} from '../../types';

type Section = 'template' | 'agentserver' | 'sandbox';

type AgentForm = {
  container_id: string;
  name: string;
  image: string;
  image_pull_policy: string;
  sse_port: number;
  http_port: string;
  env_text: string;
  env_from_json: string;
  cpu_request: string;
  memory_request: string;
  cpu_limit: string;
  memory_limit: string;
  volume_mounts_json: string;
  run_as_user: string;
  run_as_group: string;
  health_path: string;
  readiness_initial_delay: number;
  readiness_period: number;
};

type SandboxForm = {
  container_id: string;
  name: string;
  image: string;
  image_pull_policy: string;
  port: string;
  env_text: string;
  env_from_json: string;
  cpu_request: string;
  memory_request: string;
  cpu_limit: string;
  memory_limit: string;
  volume_mounts_json: string;
  run_as_user: string;
  run_as_group: string;
  privileged: boolean;
  capabilities_add: string;
  seccomp_type: string;
  apparmor_type: string;
  probe_type: 'tcpSocket' | 'httpGet';
  probe_port: number;
  probe_path: string;
  readiness_initial_delay: number;
  readiness_period: number;
  readiness_timeout: number;
};

type TemplateForm = {
  template_name: string;
  description: string;
  namespace: string;
  node_name: string;
  pod_name: string;
  sse_path: string;
  kubeconfig: string;
  ready_timeout: number;
  ready_poll_interval: number;
  min_idle_services: number;
  service_concurrency: number;
  service_ttl: number;
  message_timeout: number;
  session_concurrency: number;
  session_ttl: number;
  volumes_json: string;
};

const INT32_MAX = 2_147_483_647;

const emptyAgent = (): AgentForm => ({
  container_id: 'c-agentserver',
  name: 'jiuwenclaw-agentserver',
  image: '',
  image_pull_policy: 'IfNotPresent',
  sse_port: 8766,
  http_port: '',
  env_text: '',
  env_from_json: '',
  cpu_request: '',
  memory_request: '',
  cpu_limit: '',
  memory_limit: '',
  volume_mounts_json: '',
  run_as_user: '',
  run_as_group: '',
  health_path: '/api/v1/health',
  readiness_initial_delay: 5,
  readiness_period: 5,
});

const emptySandbox = (): SandboxForm => ({
  container_id: 'c-jiuwenbox',
  name: 'jiuwenbox',
  image: '',
  image_pull_policy: 'IfNotPresent',
  port: '8321',
  env_text: '',
  env_from_json: '',
  cpu_request: '',
  memory_request: '',
  cpu_limit: '',
  memory_limit: '',
  volume_mounts_json: '',
  run_as_user: '',
  run_as_group: '',
  privileged: false,
  capabilities_add: '',
  seccomp_type: '',
  apparmor_type: '',
  probe_type: 'tcpSocket',
  probe_port: 8321,
  probe_path: '/health',
  readiness_initial_delay: 10,
  readiness_period: 5,
  readiness_timeout: 3,
});

const emptyTemplate = (): TemplateForm => ({
  template_name: '',
  description: '',
  namespace: 'default',
  node_name: '',
  pod_name: 'agentserver',
  sse_path: '/api/v1/events/stream',
  kubeconfig: '',
  ready_timeout: 300,
  ready_poll_interval: 2,
  min_idle_services: 0,
  service_concurrency: 2,
  service_ttl: 300,
  message_timeout: 600,
  session_concurrency: 3,
  session_ttl: 60,
  volumes_json: '',
});

function opt(v: string) {
  return v.trim() || undefined;
}

function formatJson(value: unknown): string {
  if (value == null) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return '';
  }
}

function formatEnv(env: unknown): string {
  if (Array.isArray(env)) {
    return env
      .map((item) => {
        if (!item || typeof item !== 'object') return '';
        const row = item as Record<string, unknown>;
        if (row.name == null) return '';
        return `${row.name}=${row.value ?? ''}`;
      })
      .filter(Boolean)
      .join('\n');
  }
  if (env && typeof env === 'object') {
    return Object.entries(env as Record<string, unknown>)
      .map(([k, v]) => `${k}=${v ?? ''}`)
      .join('\n');
  }
  return '';
}

function parseEnv(text: string): { ok: true; value?: { name: string; value: string }[] } | { ok: false } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: undefined };
  const out: { name: string; value: string }[] = [];
  for (const line of trimmed.split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#')) continue;
    const eq = s.indexOf('=');
    if (eq <= 0) return { ok: false };
    out.push({ name: s.slice(0, eq).trim(), value: s.slice(eq + 1) });
  }
  return { ok: true, value: out.length ? out : undefined };
}

function parseJsonArray(text: string): { ok: true; value?: Record<string, unknown>[] } | { ok: false } {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: undefined };
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!Array.isArray(parsed)) return { ok: false };
    if (!parsed.every((item) => item != null && typeof item === 'object' && !Array.isArray(item))) {
      return { ok: false };
    }
    return { ok: true, value: parsed as Record<string, unknown>[] };
  } catch {
    return { ok: false };
  }
}

function parseOptionalInt(text: string): number | undefined {
  const t = text.trim();
  if (!t) return undefined;
  const n = Number(t);
  if (!Number.isInteger(n) || n < 0) return NaN;
  return n;
}

function envToMap(list?: { name: string; value: string }[]): Record<string, string> | undefined {
  if (!list?.length) return undefined;
  const out: Record<string, string> = {};
  for (const row of list) out[row.name] = row.value;
  return out;
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && (
        <span className="text-danger ml-0.5" aria-hidden="true">
          *
        </span>
      )}
    </label>
  );
}

function SectionCard({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">{title}</div>
          {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{children}</div>
    </div>
  );
}

function containerFromRecord(raw: Record<string, unknown> | undefined, role: 'agent'): AgentForm;
function containerFromRecord(raw: Record<string, unknown> | undefined, role: 'sandbox'): SandboxForm;
function containerFromRecord(
  raw: Record<string, unknown> | undefined,
  role: 'agent' | 'sandbox',
): AgentForm | SandboxForm {
  if (role === 'agent') {
    const form = emptyAgent();
    if (!raw) return form;
    form.container_id = String(raw.container_id ?? form.container_id);
    form.name = String(raw.name ?? form.name);
    form.image = String(raw.image ?? '');
    form.image_pull_policy = String(raw.imagePullPolicy ?? raw.image_pull_policy ?? 'IfNotPresent');
    const ports = Array.isArray(raw.ports) ? raw.ports : [];
    for (const p of ports) {
      if (!p || typeof p !== 'object') continue;
      const port = p as Record<string, unknown>;
      const n = Number(port.containerPort ?? port.container_port);
      if (port.name === 'sse' && Number.isFinite(n)) form.sse_port = n;
      if (port.name === 'http' && Number.isFinite(n)) form.http_port = String(n);
    }
    form.env_text = formatEnv(raw.env);
    form.env_from_json = formatJson(raw.envFrom ?? raw.env_from);
    form.volume_mounts_json = formatJson(raw.volumeMounts ?? raw.volume_mounts);
    const res = (raw.resources as Record<string, unknown> | undefined) ?? {};
    const req = (res.requests as Record<string, unknown> | undefined) ?? {};
    const lim = (res.limits as Record<string, unknown> | undefined) ?? {};
    form.cpu_request = req.cpu != null ? String(req.cpu) : '';
    form.memory_request = req.memory != null ? String(req.memory) : '';
    form.cpu_limit = lim.cpu != null ? String(lim.cpu) : '';
    form.memory_limit = lim.memory != null ? String(lim.memory) : '';
    const sc = (raw.securityContext ?? raw.security_context) as Record<string, unknown> | undefined;
    if (sc) {
      form.run_as_user = sc.runAsUser != null ? String(sc.runAsUser) : '';
      form.run_as_group = sc.runAsGroup != null ? String(sc.runAsGroup) : '';
    }
    const probe = (raw.readinessProbe ?? raw.readiness_probe) as Record<string, unknown> | undefined;
    if (probe) {
      const http = (probe.httpGet ?? probe.http_get) as Record<string, unknown> | undefined;
      if (http?.path != null) form.health_path = String(http.path);
      if (http?.port != null && Number.isFinite(Number(http.port))) {
        form.sse_port = Number(http.port);
      }
      if (probe.initialDelaySeconds != null) {
        form.readiness_initial_delay = Number(probe.initialDelaySeconds);
      }
      if (probe.periodSeconds != null) form.readiness_period = Number(probe.periodSeconds);
    }
    return form;
  }

  const form = emptySandbox();
  if (!raw) return form;
  form.container_id = String(raw.container_id ?? form.container_id);
  form.name = String(raw.name ?? form.name);
  form.image = String(raw.image ?? '');
  form.image_pull_policy = String(raw.imagePullPolicy ?? raw.image_pull_policy ?? 'IfNotPresent');
  const ports = Array.isArray(raw.ports) ? raw.ports : [];
  const first = ports[0] as Record<string, unknown> | undefined;
  if (first?.containerPort != null) form.port = String(first.containerPort);
  form.env_text = formatEnv(raw.env);
  form.env_from_json = formatJson(raw.envFrom ?? raw.env_from);
  form.volume_mounts_json = formatJson(raw.volumeMounts ?? raw.volume_mounts);
  const res = (raw.resources as Record<string, unknown> | undefined) ?? {};
  const req = (res.requests as Record<string, unknown> | undefined) ?? {};
  const lim = (res.limits as Record<string, unknown> | undefined) ?? {};
  form.cpu_request = req.cpu != null ? String(req.cpu) : '';
  form.memory_request = req.memory != null ? String(req.memory) : '';
  form.cpu_limit = lim.cpu != null ? String(lim.cpu) : '';
  form.memory_limit = lim.memory != null ? String(lim.memory) : '';
  const sc = (raw.securityContext ?? raw.security_context) as Record<string, unknown> | undefined;
  if (sc) {
    form.run_as_user = sc.runAsUser != null ? String(sc.runAsUser) : '';
    form.run_as_group = sc.runAsGroup != null ? String(sc.runAsGroup) : '';
    form.privileged = Boolean(sc.privileged);
    const caps = sc.capabilities as Record<string, unknown> | undefined;
    const add = caps?.add;
    if (Array.isArray(add)) form.capabilities_add = add.map(String).join(', ');
    const sec = sc.seccompProfile as Record<string, unknown> | undefined;
    if (sec?.type) form.seccomp_type = String(sec.type);
    const app = sc.appArmorProfile as Record<string, unknown> | undefined;
    if (app?.type) form.apparmor_type = String(app.type);
  }
  const probe = (raw.readinessProbe ?? raw.readiness_probe) as Record<string, unknown> | undefined;
  if (probe) {
    if (probe.tcpSocket || probe.tcp_socket) {
      form.probe_type = 'tcpSocket';
      const tcp = (probe.tcpSocket ?? probe.tcp_socket) as Record<string, unknown>;
      if (tcp.port != null) form.probe_port = Number(tcp.port);
    } else if (probe.httpGet || probe.http_get) {
      form.probe_type = 'httpGet';
      const http = (probe.httpGet ?? probe.http_get) as Record<string, unknown>;
      if (http.port != null) form.probe_port = Number(http.port);
      if (http.path != null) form.probe_path = String(http.path);
    }
    if (probe.initialDelaySeconds != null) {
      form.readiness_initial_delay = Number(probe.initialDelaySeconds);
    }
    if (probe.periodSeconds != null) form.readiness_period = Number(probe.periodSeconds);
    if (probe.timeoutSeconds != null) form.readiness_timeout = Number(probe.timeoutSeconds);
  }
  return form;
}

function hydrateFromTemplateRow(row: ServiceConfigTemplate): {
  template: TemplateForm;
  agent: AgentForm;
  sandbox: SandboxForm;
  scopes: Record<string, unknown>[];
  sourceTemplateId?: string;
} {
  const sync = (row.data?.config_sync as Record<string, unknown> | undefined) ?? {};
  const containers = Array.isArray(sync.containers)
    ? (sync.containers as Record<string, unknown>[])
    : [];
  const byId = new Map(
    containers
      .filter((c) => c && typeof c.container_id === 'string')
      .map((c) => [String(c.container_id), c]),
  );
  const mainId = row.main_container_id || '';
  const sideId = row.sidecar_container_ids?.[0] || '';
  let agentRaw = mainId ? byId.get(mainId) : undefined;
  let sandboxRaw = sideId ? byId.get(sideId) : undefined;
  if (!agentRaw && containers.length) {
    agentRaw = containers.find((c) => String(c.container_id) === mainId) ?? containers[0];
  }
  if (!sandboxRaw && containers.length > 1) {
    sandboxRaw =
      containers.find((c) => String(c.container_id) === sideId) ??
      containers.find((c) => c !== agentRaw);
  }

  const template: TemplateForm = {
    template_name: row.template_name,
    description: row.description ?? '',
    namespace: row.namespace || 'default',
    node_name: row.node_name ?? '',
    pod_name: row.pod_name || 'agentserver',
    sse_path: row.sse_path || '/api/v1/events/stream',
    kubeconfig: row.kubeconfig ?? '',
    ready_timeout: row.ready_timeout,
    ready_poll_interval: row.ready_poll_interval,
    min_idle_services: row.min_idle_services,
    service_concurrency: row.service_concurrency,
    service_ttl: row.service_ttl,
    message_timeout: row.message_timeout,
    session_concurrency: row.session_concurrency,
    session_ttl: row.session_ttl,
    volumes_json: formatJson(row.volumes),
  };

  let agent = containerFromRecord(agentRaw, 'agent');
  let sandbox = containerFromRecord(sandboxRaw, 'sandbox');

  // 无 stored containers 时用模板内联列回填主容器
  if (!agentRaw) {
    agent = {
      ...agent,
      container_id: row.main_container_id || agent.container_id,
      name: row.container_name || agent.name,
      image: row.agent_image || '',
      image_pull_policy: row.image_pull_policy || 'IfNotPresent',
      sse_port: row.sse_port || row.container_port || 8080,
      env_text: formatEnv(row.agent_env),
      cpu_request: row.agent_cpu_request ?? '',
      memory_request: row.agent_memory_request ?? '',
      cpu_limit: row.agent_cpu_limit ?? '',
      memory_limit: row.agent_memory_limit ?? '',
      run_as_user: row.run_as_user != null ? String(row.run_as_user) : '',
      run_as_group: row.run_as_group != null ? String(row.run_as_group) : '',
      health_path: row.health_path || '/api/v1/health',
      readiness_initial_delay: row.readiness_initial_delay,
      readiness_period: row.readiness_period,
    };
  }
  if (!sandboxRaw && row.sidecar_container_ids?.[0]) {
    sandbox.container_id = row.sidecar_container_ids[0];
  }

  return {
    template,
    agent,
    sandbox,
    scopes: Array.isArray(sync.scopes) ? (sync.scopes as Record<string, unknown>[]) : [],
    sourceTemplateId:
      typeof sync.source_template_id === 'string' ? sync.source_template_id : undefined,
  };
}

function buildAgentWire(agent: AgentForm, env: { name: string; value: string }[] | undefined, envFrom?: Record<string, unknown>[], mounts?: Record<string, unknown>[]) {
  const ports: Record<string, unknown>[] = [
    { name: 'sse', containerPort: agent.sse_port },
  ];
  const httpPort = parseOptionalInt(agent.http_port);
  if (httpPort != null && !Number.isNaN(httpPort)) {
    ports.push({ name: 'http', containerPort: httpPort });
  }
  const wire: Record<string, unknown> = {
    container_id: agent.container_id.trim(),
    name: agent.name.trim(),
    image: agent.image.trim(),
    imagePullPolicy: agent.image_pull_policy || 'IfNotPresent',
    ports,
  };
  if (env?.length) wire.env = env;
  if (envFrom?.length) wire.envFrom = envFrom;
  if (mounts?.length) wire.volumeMounts = mounts;
  const resources: Record<string, unknown> = {};
  const requests: Record<string, string> = {};
  const limits: Record<string, string> = {};
  if (agent.cpu_request.trim()) requests.cpu = agent.cpu_request.trim();
  if (agent.memory_request.trim()) requests.memory = agent.memory_request.trim();
  if (agent.cpu_limit.trim()) limits.cpu = agent.cpu_limit.trim();
  if (agent.memory_limit.trim()) limits.memory = agent.memory_limit.trim();
  if (Object.keys(requests).length) resources.requests = requests;
  if (Object.keys(limits).length) resources.limits = limits;
  if (Object.keys(resources).length) wire.resources = resources;
  const runAsUser = parseOptionalInt(agent.run_as_user);
  const runAsGroup = parseOptionalInt(agent.run_as_group);
  if (runAsUser != null || runAsGroup != null) {
    wire.securityContext = {
      ...(runAsUser != null && !Number.isNaN(runAsUser) ? { runAsUser } : {}),
      ...(runAsGroup != null && !Number.isNaN(runAsGroup) ? { runAsGroup } : {}),
    };
  }
  wire.readinessProbe = {
    httpGet: { path: agent.health_path.trim() || '/health', port: agent.sse_port },
    initialDelaySeconds: agent.readiness_initial_delay,
    periodSeconds: agent.readiness_period,
  };
  return wire;
}

function buildSandboxWire(
  sandbox: SandboxForm,
  env: { name: string; value: string }[] | undefined,
  envFrom?: Record<string, unknown>[],
  mounts?: Record<string, unknown>[],
) {
  const wire: Record<string, unknown> = {
    container_id: sandbox.container_id.trim(),
    name: sandbox.name.trim(),
    image: sandbox.image.trim(),
    imagePullPolicy: sandbox.image_pull_policy || 'IfNotPresent',
  };
  const port = parseOptionalInt(sandbox.port);
  if (port != null && !Number.isNaN(port)) {
    wire.ports = [{ containerPort: port }];
  }
  if (env?.length) wire.env = env;
  if (envFrom?.length) wire.envFrom = envFrom;
  if (mounts?.length) wire.volumeMounts = mounts;
  const resources: Record<string, unknown> = {};
  const requests: Record<string, string> = {};
  const limits: Record<string, string> = {};
  if (sandbox.cpu_request.trim()) requests.cpu = sandbox.cpu_request.trim();
  if (sandbox.memory_request.trim()) requests.memory = sandbox.memory_request.trim();
  if (sandbox.cpu_limit.trim()) limits.cpu = sandbox.cpu_limit.trim();
  if (sandbox.memory_limit.trim()) limits.memory = sandbox.memory_limit.trim();
  if (Object.keys(requests).length) resources.requests = requests;
  if (Object.keys(limits).length) resources.limits = limits;
  if (Object.keys(resources).length) wire.resources = resources;

  const sc: Record<string, unknown> = {};
  const runAsUser = parseOptionalInt(sandbox.run_as_user);
  const runAsGroup = parseOptionalInt(sandbox.run_as_group);
  if (runAsUser != null && !Number.isNaN(runAsUser)) sc.runAsUser = runAsUser;
  if (runAsGroup != null && !Number.isNaN(runAsGroup)) sc.runAsGroup = runAsGroup;
  if (sandbox.privileged) sc.privileged = true;
  const caps = sandbox.capabilities_add
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (caps.length) sc.capabilities = { add: caps };
  if (sandbox.seccomp_type.trim()) {
    sc.seccompProfile = { type: sandbox.seccomp_type.trim() };
  }
  if (sandbox.apparmor_type.trim()) {
    sc.appArmorProfile = { type: sandbox.apparmor_type.trim() };
  }
  if (Object.keys(sc).length) wire.securityContext = sc;

  const probe: Record<string, unknown> = {
    initialDelaySeconds: sandbox.readiness_initial_delay,
    periodSeconds: sandbox.readiness_period,
    timeoutSeconds: sandbox.readiness_timeout,
  };
  if (sandbox.probe_type === 'httpGet') {
    probe.httpGet = {
      path: sandbox.probe_path.trim() || '/health',
      port: sandbox.probe_port,
    };
  } else {
    probe.tcpSocket = { port: sandbox.probe_port };
  }
  wire.readinessProbe = probe;
  return wire;
}

export function ServiceConfigTemplateEditPage({ templateId }: { templateId?: string }) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const isNew = !templateId || templateId === 'new';

  const [section, setSection] = useState<Section>('template');
  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);
  const [template, setTemplate] = useState<TemplateForm>(emptyTemplate);
  const [agent, setAgent] = useState<AgentForm>(emptyAgent);
  const [sandbox, setSandbox] = useState<SandboxForm>(emptySandbox);
  const [scopes, setScopes] = useState<Record<string, unknown>[]>([]);
  const [sourceTemplateId, setSourceTemplateId] = useState<string | undefined>();

  useEffect(() => {
    if (isNew) {
      setTemplate(emptyTemplate());
      setAgent(emptyAgent());
      setSandbox(emptySandbox());
      setScopes([]);
      setSourceTemplateId(undefined);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void ServiceConfigTemplateApi.get(templateId!)
      .then((row) => {
        if (cancelled) return;
        const hydrated = hydrateFromTemplateRow(row);
        setTemplate(hydrated.template);
        setAgent(hydrated.agent);
        setSandbox(hydrated.sandbox);
        setScopes(hydrated.scopes);
        setSourceTemplateId(hydrated.sourceTemplateId);
      })
      .catch((e) => {
        toast(
          'danger',
          t('errors.loadFailed', {
            detail: e instanceof ApiError ? e.detail : (e as Error).message,
          }),
        );
        navigate('/service-config-templates');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isNew, templateId, navigate, t]);

  const tabs = useMemo(
    () =>
      [
        { id: 'template' as const, label: t('serviceConfigTemplate.tabTemplate') },
        { id: 'agentserver' as const, label: t('serviceConfigTemplate.tabAgentServer') },
        { id: 'sandbox' as const, label: t('serviceConfigTemplate.tabSandbox') },
      ] as const,
    [t],
  );

  const updateTpl = <K extends keyof TemplateForm>(k: K, v: TemplateForm[K]) =>
    setTemplate((s) => ({ ...s, [k]: v }));
  const updateAgent = <K extends keyof AgentForm>(k: K, v: AgentForm[K]) =>
    setAgent((s) => ({ ...s, [k]: v }));
  const updateSandbox = <K extends keyof SandboxForm>(k: K, v: SandboxForm[K]) =>
    setSandbox((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!template.template_name.trim()) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: t('serviceConfigTemplate.templateName') }));
      setSection('template');
      return;
    }
    if (!agent.image.trim()) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: t('serviceConfigTemplate.agentImage') }));
      setSection('agentserver');
      return;
    }
    if (!agent.container_id.trim() || !sandbox.container_id.trim()) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: t('serviceConfigTemplate.containerId') }));
      return;
    }
    if (agent.container_id.trim() === sandbox.container_id.trim()) {
      toast('warn', t('serviceConfigTemplate.containerIdConflict'));
      return;
    }
    if (!sandbox.image.trim()) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: t('serviceConfigTemplate.sandboxImage') }));
      setSection('sandbox');
      return;
    }
    if (template.sse_path.trim() && !isValidUnixAbsPath(template.sse_path.trim())) {
      toast('warn', t('serviceConfigTemplate.pathInvalid', { field: t('serviceConfigTemplate.ssePath') }));
      setSection('template');
      return;
    }
    if (agent.health_path.trim() && !isValidUnixAbsPath(agent.health_path.trim())) {
      toast('warn', t('serviceConfigTemplate.pathInvalid', { field: t('serviceConfigTemplate.healthPath') }));
      setSection('agentserver');
      return;
    }

    for (const [value, labelKey] of [
      [agent.cpu_request, 'agentCpuRequest'],
      [agent.cpu_limit, 'agentCpuLimit'],
      [sandbox.cpu_request, 'sandboxCpuRequest'],
      [sandbox.cpu_limit, 'sandboxCpuLimit'],
    ] as const) {
      if (!isValidK8sCpu(value)) {
        toast('warn', t('serviceConfigTemplate.cpuQuantityInvalid', { field: t(`serviceConfigTemplate.${labelKey}`) }));
        return;
      }
    }
    for (const [value, labelKey] of [
      [agent.memory_request, 'agentMemoryRequest'],
      [agent.memory_limit, 'agentMemoryLimit'],
      [sandbox.memory_request, 'sandboxMemoryRequest'],
      [sandbox.memory_limit, 'sandboxMemoryLimit'],
    ] as const) {
      if (!isValidK8sMemory(value)) {
        toast('warn', t('serviceConfigTemplate.memoryQuantityInvalid', { field: t(`serviceConfigTemplate.${labelKey}`) }));
        return;
      }
    }

    const agentEnv = parseEnv(agent.env_text);
    if (!agentEnv.ok) {
      toast('warn', t('serviceConfigTemplate.agentEnvInvalid'));
      setSection('agentserver');
      return;
    }
    const sandboxEnv = parseEnv(sandbox.env_text);
    if (!sandboxEnv.ok) {
      toast('warn', t('serviceConfigTemplate.agentEnvInvalid'));
      setSection('sandbox');
      return;
    }
    const volumes = parseJsonArray(template.volumes_json);
    if (!volumes.ok) {
      toast('warn', t('serviceConfigTemplate.jsonArrayInvalid', { field: t('serviceConfigTemplate.volumes') }));
      setSection('template');
      return;
    }
    const agentEnvFrom = parseJsonArray(agent.env_from_json);
    const sandboxEnvFrom = parseJsonArray(sandbox.env_from_json);
    const agentMounts = parseJsonArray(agent.volume_mounts_json);
    const sandboxMounts = parseJsonArray(sandbox.volume_mounts_json);
    for (const [parsed, labelKey, sec] of [
      [agentEnvFrom, 'envFrom', 'agentserver'],
      [sandboxEnvFrom, 'envFrom', 'sandbox'],
      [agentMounts, 'volumeMounts', 'agentserver'],
      [sandboxMounts, 'volumeMounts', 'sandbox'],
    ] as const) {
      if (!parsed.ok) {
        toast('warn', t('serviceConfigTemplate.jsonArrayInvalid', { field: t(`serviceConfigTemplate.${labelKey}`) }));
        setSection(sec);
        return;
      }
    }
    // 循环内 return 无法收窄联合类型，这里再判一次供 TS 使用 .value
    if (!agentEnvFrom.ok || !sandboxEnvFrom.ok || !agentMounts.ok || !sandboxMounts.ok) return;

    const unsafe = findUnsafeTextField([
      { label: t('serviceConfigTemplate.templateName'), value: template.template_name },
      { label: t('serviceConfigTemplate.templateDescription'), value: template.description },
      { label: t('serviceConfigTemplate.agentImage'), value: agent.image },
      { label: t('serviceConfigTemplate.sandboxImage'), value: sandbox.image },
      { label: t('serviceConfigTemplate.namespace'), value: template.namespace },
      { label: t('serviceConfigTemplate.podName'), value: template.pod_name },
    ]);
    if (unsafe) {
      toast('warn', t('serviceConfigTemplate.unsafeText', { field: unsafe }));
      return;
    }

    const agentWire = buildAgentWire(agent, agentEnv.value, agentEnvFrom.value, agentMounts.value);
    const sandboxWire = buildSandboxWire(
      sandbox,
      sandboxEnv.value,
      sandboxEnvFrom.value,
      sandboxMounts.value,
    );
    const runAsUser = parseOptionalInt(agent.run_as_user);
    const runAsGroup = parseOptionalInt(agent.run_as_group);

    const body: ServiceConfigTemplateCreateBody = {
      template_name: template.template_name.trim(),
      description: opt(template.description),
      agent_image: agent.image.trim(),
      namespace: template.namespace.trim() || 'default',
      node_name: opt(template.node_name),
      run_as_user: runAsUser != null && !Number.isNaN(runAsUser) ? runAsUser : null,
      run_as_group: runAsGroup != null && !Number.isNaN(runAsGroup) ? runAsGroup : null,
      pod_name: template.pod_name.trim() || 'agentserver',
      container_name: agent.name.trim() || 'agent',
      container_port: agent.sse_port,
      port_name: 'sse',
      sse_port: agent.sse_port,
      sse_path: template.sse_path.trim() || '/api/v1/events/stream',
      health_path: agent.health_path.trim() || '/api/v1/health',
      agent_env: envToMap(agentEnv.value),
      image_pull_policy: (agent.image_pull_policy || 'IfNotPresent') as
        | 'Always'
        | 'IfNotPresent'
        | 'Never',
      kubeconfig: opt(template.kubeconfig),
      readiness_initial_delay: agent.readiness_initial_delay,
      readiness_period: agent.readiness_period,
      ready_timeout: template.ready_timeout,
      ready_poll_interval: template.ready_poll_interval,
      agent_cpu_request: opt(agent.cpu_request),
      agent_memory_request: opt(agent.memory_request),
      agent_cpu_limit: opt(agent.cpu_limit),
      agent_memory_limit: opt(agent.memory_limit),
      main_container_id: agent.container_id.trim(),
      sidecar_container_ids: [sandbox.container_id.trim()],
      volumes: volumes.value,
      min_idle_services: template.min_idle_services,
      service_concurrency: template.service_concurrency,
      service_ttl: template.service_ttl,
      message_timeout: template.message_timeout,
      session_concurrency: template.session_concurrency,
      session_ttl: template.session_ttl,
      enabled: true,
      data: {
        config_sync: {
          containers: [agentWire, sandboxWire],
          scopes,
          ...(sourceTemplateId ? { source_template_id: sourceTemplateId } : {}),
        },
      },
    };

    setSaving(true);
    try {
      if (isNew) {
        await ServiceConfigTemplateApi.create(body);
      } else {
        await ServiceConfigTemplateApi.update(templateId!, body);
      }
      toast('success', t('success.saved'));
      navigate('/service-config-templates');
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', {
          detail: e instanceof ApiError ? e.detail : (e as Error).message,
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="p-4 text-sm text-muted">{t('common.loading')}</div>;
  }

  const pageTitle = isNew
    ? t('serviceConfigTemplate.new')
    : template.template_name.trim() || t('serviceConfigTemplate.edit');

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-x-auto">
      <div className="page-header flex w-full min-w-0 shrink-0 flex-col items-stretch gap-3 lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-center lg:gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 lg:justify-self-start">
          <button
            type="button"
            className="btn ghost sm shrink-0"
            onClick={() => navigate('/service-config-templates')}
            aria-label={t('serviceConfigTemplate.title')}
            title={t('serviceConfigTemplate.backToList')}
          >
            ←
          </button>
          <div className="min-w-0 max-w-full flex-1 sm:max-w-[16rem] sm:flex-none">
            <div className="page-title truncate" title={pageTitle}>
              {pageTitle}
            </div>
            <div
              className="text-[11px] text-muted mono truncate"
              title={isNew ? t('serviceConfigTemplate.editSubtitle') : templateId}
            >
              {isNew ? t('serviceConfigTemplate.editSubtitle') : templateId}
            </div>
          </div>
        </div>

        <div className="tabs-bar max-w-full shrink-0 self-center overflow-x-auto lg:justify-self-center">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab ${section === tab.id ? 'active' : ''}`}
              onClick={() => setSection(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 lg:justify-self-end">
          <button className="btn primary sm" disabled={saving} onClick={() => void submit()}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </div>
      </div>

      <div className="w-full min-w-0 shrink-0">
      {section === 'template' && (
        <SectionCard
          title={t('serviceConfigTemplate.tabTemplate')}
          hint={t('serviceConfigTemplate.tabTemplateHint')}
        >
          <div className="md:col-span-2">
            <FieldLabel required>{t('serviceConfigTemplate.templateName')}</FieldLabel>
            <LimitedTextInput
              value={template.template_name}
              maxLength={128}
              onChange={(v) => updateTpl('template_name', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('serviceConfigTemplate.templateDescription')}</FieldLabel>
            <LimitedTextInput
              value={template.description}
              maxLength={512}
              onChange={(v) => updateTpl('description', v)}
            />
          </div>
          <div>
            <FieldLabel>{t('serviceConfigTemplate.namespace')}</FieldLabel>
            <LimitedTextInput
              value={template.namespace}
              maxLength={128}
              onChange={(v) => updateTpl('namespace', v)}
            />
          </div>
          <div>
            <FieldLabel>{t('serviceConfigTemplate.nodeName')}</FieldLabel>
            <LimitedTextInput
              value={template.node_name}
              maxLength={128}
              onChange={(v) => updateTpl('node_name', v)}
            />
          </div>
          <div>
            <FieldLabel>{t('serviceConfigTemplate.podName')}</FieldLabel>
            <LimitedTextInput
              value={template.pod_name}
              maxLength={128}
              onChange={(v) => updateTpl('pod_name', v)}
            />
          </div>
          <div>
            <FieldLabel>{t('serviceConfigTemplate.ssePath')}</FieldLabel>
            <input
              className="input"
              value={template.sse_path}
              maxLength={128}
              onChange={(e) => updateTpl('sse_path', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('serviceConfigTemplate.kubeconfig')}</FieldLabel>
            <LimitedTextInput
              value={template.kubeconfig}
              maxLength={512}
              onChange={(v) => updateTpl('kubeconfig', v)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.readyTimeout')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.ready_timeout}
              onChange={(e) => updateTpl('ready_timeout', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.readyPollInterval')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.ready_poll_interval}
              onChange={(e) => updateTpl('ready_poll_interval', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.minIdleServices')}</label>
            <input
              className="input"
              type="number"
              min={0}
              max={INT32_MAX}
              value={template.min_idle_services}
              onChange={(e) => updateTpl('min_idle_services', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.serviceConcurrency')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.service_concurrency}
              onChange={(e) => updateTpl('service_concurrency', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.serviceTtl')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.service_ttl}
              onChange={(e) => updateTpl('service_ttl', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sessionConcurrency')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.session_concurrency}
              onChange={(e) => updateTpl('session_concurrency', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sessionTtl')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.session_ttl}
              onChange={(e) => updateTpl('session_ttl', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.messageTimeout')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={INT32_MAX}
              value={template.message_timeout}
              onChange={(e) => updateTpl('message_timeout', Number(e.target.value))}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.volumes')}</label>
            <textarea
              className="input min-h-[8rem] font-mono text-[12px]"
              placeholder='[{"name":"data","persistentVolumeClaim":{"claimName":"jiuwenclaw-pvc"}}]'
              value={template.volumes_json}
              onChange={(e) => updateTpl('volumes_json', e.target.value)}
            />
            <div className="text-[11px] text-muted mt-1">{t('serviceConfigTemplate.volumesHint')}</div>
          </div>
        </SectionCard>
      )}

      {section === 'agentserver' && (
        <SectionCard
          title={t('serviceConfigTemplate.tabAgentServer')}
          hint={t('serviceConfigTemplate.tabAgentServerHint')}
        >
          <div>
            <FieldLabel required>{t('serviceConfigTemplate.containerId')}</FieldLabel>
            <LimitedTextInput
              value={agent.container_id}
              maxLength={100}
              onChange={(v) => updateAgent('container_id', v)}
            />
          </div>
          <div>
            <FieldLabel required>{t('serviceConfigTemplate.containerName')}</FieldLabel>
            <LimitedTextInput
              value={agent.name}
              maxLength={128}
              onChange={(v) => updateAgent('name', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel required>{t('serviceConfigTemplate.agentImage')}</FieldLabel>
            <LimitedTextInput
              value={agent.image}
              maxLength={512}
              onChange={(v) => updateAgent('image', v)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.imagePullPolicy')}</label>
            <select
              className="select"
              value={agent.image_pull_policy}
              onChange={(e) => updateAgent('image_pull_policy', e.target.value)}
            >
              <option value="IfNotPresent">IfNotPresent</option>
              <option value="Always">Always</option>
              <option value="Never">Never</option>
            </select>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.ssePort')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={65535}
              value={agent.sse_port}
              onChange={(e) => updateAgent('sse_port', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.httpPort')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={65535}
              placeholder="optional"
              value={agent.http_port}
              onChange={(e) => updateAgent('http_port', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.healthPath')}</label>
            <input
              className="input"
              value={agent.health_path}
              onChange={(e) => updateAgent('health_path', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.readinessInitialDelay')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={agent.readiness_initial_delay}
              onChange={(e) => updateAgent('readiness_initial_delay', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.readinessPeriod')}</label>
            <input
              className="input"
              type="number"
              min={1}
              value={agent.readiness_period}
              onChange={(e) => updateAgent('readiness_period', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.runAsUser')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={agent.run_as_user}
              onChange={(e) => updateAgent('run_as_user', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.runAsGroup')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={agent.run_as_group}
              onChange={(e) => updateAgent('run_as_group', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.agentCpuRequest')}</label>
            <input
              className="input"
              placeholder="500m"
              value={agent.cpu_request}
              onChange={(e) => updateAgent('cpu_request', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.agentMemoryRequest')}</label>
            <input
              className="input"
              placeholder="512Mi"
              value={agent.memory_request}
              onChange={(e) => updateAgent('memory_request', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.agentCpuLimit')}</label>
            <input
              className="input"
              placeholder="2"
              value={agent.cpu_limit}
              onChange={(e) => updateAgent('cpu_limit', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.agentMemoryLimit')}</label>
            <input
              className="input"
              placeholder="2Gi"
              value={agent.memory_limit}
              onChange={(e) => updateAgent('memory_limit', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.agentEnv')}</label>
            <textarea
              className="input min-h-[5rem] font-mono text-[12px]"
              placeholder={'KEY=value'}
              value={agent.env_text}
              onChange={(e) => updateAgent('env_text', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.envFrom')}</label>
            <textarea
              className="input min-h-[4.5rem] font-mono text-[12px]"
              placeholder='[{"secretRef":{"name":"jiuwenclaw-secret-configmap"}}]'
              value={agent.env_from_json}
              onChange={(e) => updateAgent('env_from_json', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.volumeMounts')}</label>
            <textarea
              className="input min-h-[5rem] font-mono text-[12px]"
              placeholder='[{"name":"data","mountPath":"/root/.jiuwenswarm"}]'
              value={agent.volume_mounts_json}
              onChange={(e) => updateAgent('volume_mounts_json', e.target.value)}
            />
          </div>
        </SectionCard>
      )}

      {section === 'sandbox' && (
        <SectionCard
          title={t('serviceConfigTemplate.tabSandbox')}
          hint={t('serviceConfigTemplate.tabSandboxHint')}
        >
          <div>
            <FieldLabel required>{t('serviceConfigTemplate.containerId')}</FieldLabel>
            <LimitedTextInput
              value={sandbox.container_id}
              maxLength={100}
              onChange={(v) => updateSandbox('container_id', v)}
            />
          </div>
          <div>
            <FieldLabel required>{t('serviceConfigTemplate.containerName')}</FieldLabel>
            <LimitedTextInput
              value={sandbox.name}
              maxLength={128}
              onChange={(v) => updateSandbox('name', v)}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel required>{t('serviceConfigTemplate.sandboxImage')}</FieldLabel>
            <LimitedTextInput
              value={sandbox.image}
              maxLength={512}
              onChange={(v) => updateSandbox('image', v)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.imagePullPolicy')}</label>
            <select
              className="select"
              value={sandbox.image_pull_policy}
              onChange={(e) => updateSandbox('image_pull_policy', e.target.value)}
            >
              <option value="IfNotPresent">IfNotPresent</option>
              <option value="Always">Always</option>
              <option value="Never">Never</option>
            </select>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sandboxPort')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={65535}
              value={sandbox.port}
              onChange={(e) => updateSandbox('port', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.probeType')}</label>
            <select
              className="select"
              value={sandbox.probe_type}
              onChange={(e) =>
                updateSandbox('probe_type', e.target.value as SandboxForm['probe_type'])
              }
            >
              <option value="tcpSocket">tcpSocket</option>
              <option value="httpGet">httpGet</option>
            </select>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.probePort')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={65535}
              value={sandbox.probe_port}
              onChange={(e) => updateSandbox('probe_port', Number(e.target.value))}
            />
          </div>
          {sandbox.probe_type === 'httpGet' && (
            <div>
              <label className="label">{t('serviceConfigTemplate.healthPath')}</label>
              <input
                className="input"
                value={sandbox.probe_path}
                onChange={(e) => updateSandbox('probe_path', e.target.value)}
              />
            </div>
          )}
          <div>
            <label className="label">{t('serviceConfigTemplate.readinessInitialDelay')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={sandbox.readiness_initial_delay}
              onChange={(e) => updateSandbox('readiness_initial_delay', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.readinessPeriod')}</label>
            <input
              className="input"
              type="number"
              min={1}
              value={sandbox.readiness_period}
              onChange={(e) => updateSandbox('readiness_period', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.probeTimeout')}</label>
            <input
              className="input"
              type="number"
              min={1}
              max={300}
              value={sandbox.readiness_timeout}
              onChange={(e) => updateSandbox('readiness_timeout', Number(e.target.value))}
            />
          </div>
          <div className="flex items-end gap-2 pb-1">
            <label className="label flex items-center gap-2">
              <input
                type="checkbox"
                checked={sandbox.privileged}
                onChange={(e) => updateSandbox('privileged', e.target.checked)}
              />
              {t('serviceConfigTemplate.privileged')}
            </label>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.capabilitiesAdd')}</label>
            <input
              className="input"
              placeholder="SYS_ADMIN, NET_ADMIN"
              value={sandbox.capabilities_add}
              onChange={(e) => updateSandbox('capabilities_add', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.seccompType')}</label>
            <select
              className="select"
              value={sandbox.seccomp_type}
              onChange={(e) => updateSandbox('seccomp_type', e.target.value)}
            >
              <option value="">—</option>
              <option value="Unconfined">Unconfined</option>
              <option value="RuntimeDefault">RuntimeDefault</option>
            </select>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.apparmorType')}</label>
            <select
              className="select"
              value={sandbox.apparmor_type}
              onChange={(e) => updateSandbox('apparmor_type', e.target.value)}
            >
              <option value="">—</option>
              <option value="Unconfined">Unconfined</option>
              <option value="RuntimeDefault">RuntimeDefault</option>
            </select>
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.runAsUser')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={sandbox.run_as_user}
              onChange={(e) => updateSandbox('run_as_user', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.runAsGroup')}</label>
            <input
              className="input"
              type="number"
              min={0}
              value={sandbox.run_as_group}
              onChange={(e) => updateSandbox('run_as_group', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sandboxCpuRequest')}</label>
            <input
              className="input"
              placeholder="250m"
              value={sandbox.cpu_request}
              onChange={(e) => updateSandbox('cpu_request', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sandboxMemoryRequest')}</label>
            <input
              className="input"
              placeholder="256Mi"
              value={sandbox.memory_request}
              onChange={(e) => updateSandbox('memory_request', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sandboxCpuLimit')}</label>
            <input
              className="input"
              placeholder="1"
              value={sandbox.cpu_limit}
              onChange={(e) => updateSandbox('cpu_limit', e.target.value)}
            />
          </div>
          <div>
            <label className="label">{t('serviceConfigTemplate.sandboxMemoryLimit')}</label>
            <input
              className="input"
              placeholder="1Gi"
              value={sandbox.memory_limit}
              onChange={(e) => updateSandbox('memory_limit', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.agentEnv')}</label>
            <textarea
              className="input min-h-[5rem] font-mono text-[12px]"
              placeholder={'KEY=value'}
              value={sandbox.env_text}
              onChange={(e) => updateSandbox('env_text', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.envFrom')}</label>
            <textarea
              className="input min-h-[4.5rem] font-mono text-[12px]"
              value={sandbox.env_from_json}
              onChange={(e) => updateSandbox('env_from_json', e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">{t('serviceConfigTemplate.volumeMounts')}</label>
            <textarea
              className="input min-h-[5rem] font-mono text-[12px]"
              value={sandbox.volume_mounts_json}
              onChange={(e) => updateSandbox('volume_mounts_json', e.target.value)}
            />
          </div>
        </SectionCard>
      )}
      </div>
    </div>
  );
}
