import type { ContainerTemplate, ContainerTemplateCreateBody } from '../types';
import { isValidK8sCpu, isValidK8sMemory } from './k8sResource';
import { runtimeTemplateData } from './runtimeTemplateForm';

type Source = ContainerTemplateCreateBody | ContainerTemplate;
type JsonObject = Record<string, unknown>;
const INT32_MAX = 2_147_483_647;

export interface ContainerTemplateForm {
  template_name: string;
  description: string;
  container_id: string;
  name: string;
  image: string;
  image_pull_policy: string;
  enabled: boolean;
  ports_json: string;
  env_text: string;
  env_from_json: string;
  command_json: string;
  args_json: string;
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
  probe_enabled: boolean;
  probe_type: 'tcpSocket' | 'httpGet' | 'custom';
  probe_port: string;
  probe_path: string;
  readiness_initial_delay: number;
  readiness_period: number;
  readiness_timeout: number;
  /** Full JSON objects; changed structured controls override matching properties. */
  security_extra_json: string;
  probe_extra_json: string;
  resource_extra_json: string;
}

function object(value: unknown): JsonObject {
  return value != null && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}
function json(value: unknown): string {
  return value == null ? '' : JSON.stringify(value, null, 2);
}
function text(value: unknown): string {
  return value == null ? '' : String(value);
}
function envText(value: unknown): string {
  if (!Array.isArray(value)) return json(value);
  const simple = value.every(item => {
    const row = object(item);
    return Object.keys(row).every(key => key === 'name' || key === 'value')
      && typeof row.name === 'string' && !/[=\r\n]/.test(row.name)
      && typeof row.value === 'string' && !/[\r\n]/.test(row.value);
  });
  return simple ? value.map(item => `${item.name}=${item.value}`).join('\n') : json(value);
}

/** A source-less form deliberately has no readiness probe until one is enabled. */
export function containerTemplateForm(row?: Source | null): ContainerTemplateForm {
  const resources = object(row?.resources);
  const requests = object(resources.requests);
  const limits = object(resources.limits);
  const security = object(row?.security_context);
  const probe = object(row?.readiness_probe);
  const probeType = probe.tcpSocket || probe.tcp_socket ? 'tcpSocket'
    : probe.httpGet || probe.http_get ? 'httpGet' : Object.keys(probe).length ? 'custom' : 'httpGet';
  const target = object(probeType === 'tcpSocket' ? probe.tcpSocket ?? probe.tcp_socket : probe.httpGet ?? probe.http_get);
  return {
    template_name: row?.template_name ?? '', description: row?.description ?? '',
    container_id: row?.container_id ?? '', name: row?.name ?? 'agent', image: row?.image ?? '',
    image_pull_policy: row?.image_pull_policy ?? 'IfNotPresent', enabled: row?.enabled ?? true,
    ports_json: json(row?.ports), env_text: envText(row?.env), env_from_json: json(row?.env_from),
    command_json: json(row?.command), args_json: json(row?.args), volume_mounts_json: json(row?.volume_mounts),
    cpu_request: text(requests.cpu), memory_request: text(requests.memory),
    cpu_limit: text(limits.cpu), memory_limit: text(limits.memory),
    run_as_user: text(security.runAsUser), run_as_group: text(security.runAsGroup),
    privileged: security.privileged === true,
    capabilities_add: Array.isArray(object(security.capabilities).add) ? (object(security.capabilities).add as unknown[]).map(String).join(', ') : '',
    seccomp_type: text(object(security.seccompProfile).type), apparmor_type: text(object(security.appArmorProfile).type),
    probe_enabled: row?.readiness_probe != null && Object.keys(probe).length > 0,
    probe_type: probeType, probe_port: text(target.port ?? 8080), probe_path: text(target.path ?? '/health'),
    readiness_initial_delay: Number(probe.initialDelaySeconds ?? 5),
    readiness_period: Number(probe.periodSeconds ?? 5), readiness_timeout: Number(probe.timeoutSeconds ?? 3),
    security_extra_json: json(row?.security_context), probe_extra_json: json(row?.readiness_probe),
    resource_extra_json: json(row?.resources),
  };
}

function parse(text: string, label: string): unknown {
  try { return JSON.parse(text); } catch { throw new Error(`${label}：请输入有效 JSON。`); }
}
function parseObject(value: string, label: string): JsonObject {
  if (!value.trim()) return {};
  const parsed = parse(value, label);
  if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label}：应为 JSON 对象。`);
  return parsed as JsonObject;
}
function parseArray(value: string, label: string, strings = false): unknown[] {
  if (!value.trim()) return [];
  const parsed = parse(value, label);
  if (!Array.isArray(parsed) || !parsed.every(item => strings ? typeof item === 'string' : item != null && typeof item === 'object' && !Array.isArray(item))) {
    throw new Error(`${label}：应为${strings ? '字符串' : '对象'}数组。`);
  }
  return parsed;
}
function parseEnv(value: string): unknown[] {
  if (value.trim().startsWith('[')) return parseArray(value, '环境变量');
  const entries: { name: string; value: string }[] = [];
  for (const line of value.split(/\r?\n/)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue;
    const index = line.indexOf('=');
    const name = index < 0 ? '' : line.slice(0, index).trim();
    if (!name) throw new Error('环境变量：每行应为 KEY=value，或使用 JSON 数组。');
    entries.push({ name, value: line.slice(index + 1) });
  }
  return entries;
}
function integer(value: unknown, label: string, min = 0, max = INT32_MAX): number {
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${label}：请输入 ${min}–${max} 之间的整数。`);
  return number;
}
function updateNested(target: JsonObject, key: string, inner: string, value: unknown) {
  const nested = { ...object(target[key]) };
  if (value === undefined) delete nested[inner]; else nested[inner] = value;
  if (Object.keys(nested).length) target[key] = nested; else delete target[key];
}

/** Round-trip all source properties; only changed controls override the full JSON editors. */
export function containerTemplateBody(form: ContainerTemplateForm, original?: Source | null): ContainerTemplateCreateBody {
  const baseline = containerTemplateForm(original);
  const changed = (key: keyof ContainerTemplateForm) => form[key] !== baseline[key];
  const array = (key: 'ports_json' | 'env_from_json' | 'volume_mounts_json' | 'command_json' | 'args_json', source: unknown, label: string, strings = false) =>
    !changed(key) && source !== undefined ? structuredClone(source) : parseArray(form[key], label, strings);
  const security = parseObject(form.security_extra_json, '安全上下文');
  for (const [key, wire] of [['run_as_user', 'runAsUser'], ['run_as_group', 'runAsGroup']] as const) {
    if (changed(key)) {
      if (!form[key].trim()) delete security[wire]; else security[wire] = integer(form[key], wire);
    }
  }
  if (changed('privileged')) security.privileged = form.privileged;
  if (changed('capabilities_add')) updateNested(security, 'capabilities', 'add', form.capabilities_add.trim() ? form.capabilities_add.split(/[,;\s]+/).filter(Boolean) : undefined);
  for (const [key, wire] of [['seccomp_type', 'seccompProfile'], ['apparmor_type', 'appArmorProfile']] as const) {
    if (changed(key)) {
      if (!form[key].trim()) delete security[wire];
      else {
        updateNested(security, wire, 'type', form[key].trim());
        if (form[key].trim() !== 'Localhost') delete object(security[wire]).localhostProfile;
      }
    }
  }
  const resources = parseObject(form.resource_extra_json, '资源配额');
  for (const [key, group, name, validate] of [
    ['cpu_request', 'requests', 'cpu', isValidK8sCpu], ['cpu_limit', 'limits', 'cpu', isValidK8sCpu],
    ['memory_request', 'requests', 'memory', isValidK8sMemory], ['memory_limit', 'limits', 'memory', isValidK8sMemory],
  ] as const) {
    if (changed(key)) {
      if (!validate(form[key])) throw new Error(`${group}.${name}：资源数量格式不正确。`);
      updateNested(resources, group, name, form[key].trim() || undefined);
    }
  }
  let probe: JsonObject | null = null;
  if (form.probe_enabled) {
    probe = parseObject(form.probe_extra_json, '就绪探针');
    const newlyEnabled = !baseline.probe_enabled;
    if (form.probe_type !== 'custom') {
      const rebuild = changed('probe_type') || newlyEnabled;
      const wire = form.probe_type;
      const alias = wire === 'httpGet' ? 'http_get' : 'tcp_socket';
      const target = rebuild ? {} : { ...object(probe[wire] ?? probe[alias]) };
      if (rebuild || changed('probe_port')) {
        const port = form.probe_port.trim();
        if (!port) throw new Error('探针端口不能为空。');
        target.port = /^\d+$/.test(port) ? integer(port, '探针端口', 1, 65535) : port;
      }
      if (wire === 'httpGet' && (rebuild || changed('probe_path'))) {
        const path = form.probe_path.trim();
        if (!path.startsWith('/')) throw new Error('HTTP 探针路径必须以 / 开头。');
        target.path = path;
      }
      if (rebuild || changed('probe_port') || changed('probe_path')) {
        for (const name of ['tcpSocket', 'tcp_socket', 'httpGet', 'http_get', 'exec', 'grpc']) delete probe[name];
        probe[wire] = target;
      }
    } else if (!probe.exec && !probe.grpc && !probe.httpGet && !probe.tcpSocket) {
      throw new Error('自定义就绪探针需要在完整 JSON 配置中填写 exec、grpc、httpGet 或 tcpSocket。');
    }
    for (const [key, wire, min] of [
      ['readiness_initial_delay', 'initialDelaySeconds', 0], ['readiness_period', 'periodSeconds', 1], ['readiness_timeout', 'timeoutSeconds', 1],
    ] as const) {
      if (changed(key) || newlyEnabled) probe[wire] = integer(form[key], wire, min);
    }
  }
  const preserveNull = (value: JsonObject, source: unknown, keys: (keyof ContainerTemplateForm)[]) =>
    source == null && keys.every(key => !changed(key)) && !Object.keys(value).length ? null : value;
  return {
    template_name: form.template_name.trim(), description: form.description.trim(), container_id: form.container_id.trim(),
    name: form.name.trim(), image: form.image.trim(), image_pull_policy: form.image_pull_policy,
    enabled: form.enabled,
    ports: array('ports_json', original?.ports, '端口') as ContainerTemplateCreateBody['ports'],
    env: !changed('env_text') && original?.env !== undefined ? structuredClone(original.env) : parseEnv(form.env_text),
    env_from: array('env_from_json', original?.env_from, '环境变量引用') as ContainerTemplateCreateBody['env_from'],
    command: array('command_json', original?.command, '启动命令', true) as ContainerTemplateCreateBody['command'],
    args: array('args_json', original?.args, '启动参数', true) as ContainerTemplateCreateBody['args'],
    volume_mounts: array('volume_mounts_json', original?.volume_mounts, '存储挂载') as ContainerTemplateCreateBody['volume_mounts'],
    security_context: preserveNull(security, original?.security_context, ['security_extra_json', 'run_as_user', 'run_as_group', 'privileged', 'capabilities_add', 'seccomp_type', 'apparmor_type']),
    resources: preserveNull(resources, original?.resources, ['resource_extra_json', 'cpu_request', 'cpu_limit', 'memory_request', 'memory_limit']),
    readiness_probe: probe,
    data: runtimeTemplateData(original?.data),
  };
}

export const buildContainerTemplateBody = containerTemplateBody;
