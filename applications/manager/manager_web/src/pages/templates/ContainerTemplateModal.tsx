import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ApiError, ContainerTemplateApi } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type {
  ContainerTemplate,
  ContainerTemplateCreateBody,
  ContainerTemplateUpdateBody,
} from '../../types';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidK8sCpu, isValidK8sMemory } from '../../utils/k8sResource';

interface Props {
  open: boolean;
  /** 编辑目标；null = 新建 */
  template: ContainerTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  container_id: 100,
  name: 128,
  image: 512,
} as const;

interface FormState {
  template_name: string;
  description: string;
  container_id: string;
  name: string;
  image: string;
  image_pull_policy: string;
  /** JSON 数组，形如 [{"name":"sse","containerPort":8766}] */
  ports_json: string;
  env_text: string;
  env_from_json: string;
  cpu_request: string;
  memory_request: string;
  cpu_limit: string;
  memory_limit: string;
  volume_mounts_json: string;
  /* ---- securityContext ---- */
  run_as_user: string;
  run_as_group: string;
  privileged: boolean;
  capabilities_add: string;
  seccomp_type: string;
  apparmor_type: string;
  /* ---- readinessProbe ---- */
  probe_type: 'tcpSocket' | 'httpGet';
  probe_port: number;
  probe_path: string;
  readiness_initial_delay: number;
  readiness_period: number;
  readiness_timeout: number;
}

const empty: FormState = {
  template_name: '',
  description: '',
  container_id: '',
  name: 'agent',
  image: '',
  image_pull_policy: 'IfNotPresent',
  ports_json: '',
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
  probe_type: 'httpGet',
  probe_port: 8080,
  probe_path: '/health',
  readiness_initial_delay: 5,
  readiness_period: 5,
  readiness_timeout: 3,
};

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

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required ? <span className="text-danger ml-0.5" aria-hidden>*</span> : null}
    </label>
  );
}

function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 border-t pt-2 text-[12px] font-semibold uppercase tracking-wide text-muted">
      {children}
    </div>
  );
}

function containerTemplateToForm(row: ContainerTemplate): FormState {
  const form: FormState = { ...empty };
  form.template_name = row.template_name;
  form.description = row.description ?? '';
  form.container_id = row.container_id;
  form.name = row.name || 'agent';
  form.image = row.image ?? '';
  form.image_pull_policy = row.image_pull_policy || 'IfNotPresent';
  form.ports_json = formatJson(row.ports);
  form.env_text = formatEnv(row.env);
  form.env_from_json = formatJson(row.env_from);
  form.volume_mounts_json = formatJson(row.volume_mounts);
  const res = (row.resources as Record<string, unknown> | undefined) ?? {};
  const req = (res.requests as Record<string, unknown> | undefined) ?? {};
  const lim = (res.limits as Record<string, unknown> | undefined) ?? {};
  form.cpu_request = req.cpu != null ? String(req.cpu) : '';
  form.memory_request = req.memory != null ? String(req.memory) : '';
  form.cpu_limit = lim.cpu != null ? String(lim.cpu) : '';
  form.memory_limit = lim.memory != null ? String(lim.memory) : '';
  const sc = (row.security_context as Record<string, unknown> | undefined) ?? undefined;
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
  const probe = (row.readiness_probe as Record<string, unknown> | undefined) ?? undefined;
  if (probe) {
    if (probe.tcpSocket || probe.tcp_socket) {
      form.probe_type = 'tcpSocket';
      const tcp = (probe.tcpSocket ?? probe.tcp_socket) as Record<string, unknown>;
      if (tcp.port != null && Number.isFinite(Number(tcp.port))) form.probe_port = Number(tcp.port);
    } else if (probe.httpGet || probe.http_get) {
      form.probe_type = 'httpGet';
      const http = (probe.httpGet ?? probe.http_get) as Record<string, unknown>;
      if (http.port != null && Number.isFinite(Number(http.port))) form.probe_port = Number(http.port);
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

export function ContainerTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const next = template ? containerTemplateToForm(template) : { ...empty };
    setForm(next);
    markClean(next);
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('containerTemplate.fieldRequired', { field: t('containerTemplate.templateName') }));
      return;
    }
    if (!form.container_id.trim()) {
      toast('warn', t('containerTemplate.fieldRequired', { field: t('containerTemplate.containerId') }));
      return;
    }
    if (!form.name.trim()) {
      toast('warn', t('containerTemplate.fieldRequired', { field: t('containerTemplate.containerName') }));
      return;
    }
    const ports = parseJsonArray(form.ports_json);
    if (!ports.ok) {
      toast('warn', t('containerTemplate.jsonArrayInvalid', { field: t('containerTemplate.ports') }));
      return;
    }
    const envFrom = parseJsonArray(form.env_from_json);
    if (!envFrom.ok) {
      toast('warn', t('containerTemplate.jsonArrayInvalid', { field: t('containerTemplate.envFrom') }));
      return;
    }
    const mounts = parseJsonArray(form.volume_mounts_json);
    if (!mounts.ok) {
      toast('warn', t('containerTemplate.jsonArrayInvalid', { field: t('containerTemplate.volumeMounts') }));
      return;
    }
    const env = parseEnv(form.env_text);
    if (!env.ok) {
      toast('warn', t('containerTemplate.agentEnvInvalid'));
      return;
    }
    for (const [value, key] of [
      [form.cpu_request, 'cpuRequest'],
      [form.cpu_limit, 'cpuLimit'],
    ] as const) {
      if (!isValidK8sCpu(value)) {
        toast('warn', t('containerTemplate.cpuQuantityInvalid', { field: t(`containerTemplate.${key}`) }));
        return;
      }
    }
    for (const [value, key] of [
      [form.memory_request, 'memoryRequest'],
      [form.memory_limit, 'memoryLimit'],
    ] as const) {
      if (!isValidK8sMemory(value)) {
        toast('warn', t('containerTemplate.memoryQuantityInvalid', { field: t(`containerTemplate.${key}`) }));
        return;
      }
    }
    const unsafeField = findUnsafeTextField([
      { label: t('containerTemplate.templateName'), value: form.template_name },
      { label: t('containerTemplate.description'), value: form.description },
      { label: t('containerTemplate.containerId'), value: form.container_id },
      { label: t('containerTemplate.containerName'), value: form.name },
      { label: t('containerTemplate.image'), value: form.image },
    ]);
    if (unsafeField) {
      toast('warn', t('containerTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    // 探针：httpGet / tcpSocket；httpGet 路径须为合法绝对路径
    const probe: Record<string, unknown> = {
      initialDelaySeconds: form.readiness_initial_delay,
      periodSeconds: form.readiness_period,
    };
    if (form.probe_type === 'tcpSocket') {
      probe.tcpSocket = { port: form.probe_port };
    } else {
      probe.httpGet = { path: form.probe_path.trim() || '/health', port: form.probe_port };
    }
    probe.timeoutSeconds = form.readiness_timeout;

    const resources: Record<string, unknown> = {};
    const requests: Record<string, string> = {};
    const limits: Record<string, string> = {};
    if (form.cpu_request.trim()) requests.cpu = form.cpu_request.trim();
    if (form.memory_request.trim()) requests.memory = form.memory_request.trim();
    if (form.cpu_limit.trim()) limits.cpu = form.cpu_limit.trim();
    if (form.memory_limit.trim()) limits.memory = form.memory_limit.trim();
    if (Object.keys(requests).length) resources.requests = requests;
    if (Object.keys(limits).length) resources.limits = limits;

    const securityContext: Record<string, unknown> = {};
    const runAsUser = parseOptionalInt(form.run_as_user);
    const runAsGroup = parseOptionalInt(form.run_as_group);
    if (runAsUser != null && !Number.isNaN(runAsUser)) securityContext.runAsUser = runAsUser;
    if (runAsGroup != null && !Number.isNaN(runAsGroup)) securityContext.runAsGroup = runAsGroup;
    if (form.privileged) securityContext.privileged = true;
    const caps = form.capabilities_add
      .split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (caps.length) securityContext.capabilities = { add: caps };
    if (form.seccomp_type.trim()) securityContext.seccompProfile = { type: form.seccomp_type.trim() };
    if (form.apparmor_type.trim()) securityContext.appArmorProfile = { type: form.apparmor_type.trim() };

    const body: ContainerTemplateCreateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      container_id: form.container_id.trim(),
      name: form.name.trim(),
      image: form.image.trim(),
      image_pull_policy: form.image_pull_policy || 'IfNotPresent',
      ports: ports.value,
      env: env.value,
      env_from: envFrom.value,
      resources: Object.keys(resources).length ? resources : undefined,
      volume_mounts: mounts.value,
      security_context: Object.keys(securityContext).length ? securityContext : undefined,
      readiness_probe: probe,
      enabled: template ? template.enabled : true,
    };

    setSaving(true);
    try {
      if (template) {
        await ContainerTemplateApi.update(template.template_id, body as ContainerTemplateUpdateBody);
      } else {
        await ContainerTemplateApi.create(body);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (error) {
      toast(
        'danger',
        t('errors.saveFailed', {
          detail: error instanceof ApiError ? error.detail : (error as Error).message,
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('containerTemplate.edit') : t('containerTemplate.new')}
      onClose={onClose}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <FieldLabel required>{t('containerTemplate.templateName')}</FieldLabel>
          <LimitedTextInput value={form.template_name} maxLength={FIELD_MAX_LENGTH.template_name} onChange={(value) => update('template_name', value)} />
        </div>
        <div>
          <FieldLabel required>{t('containerTemplate.containerId')}</FieldLabel>
          <LimitedTextInput value={form.container_id} maxLength={FIELD_MAX_LENGTH.container_id} onChange={(value) => update('container_id', value)} />
          <div className="text-[11px] text-muted mt-1">{t('containerTemplate.containerIdHint')}</div>
        </div>
        <div>
          <FieldLabel required>{t('containerTemplate.containerName')}</FieldLabel>
          <LimitedTextInput value={form.name} maxLength={FIELD_MAX_LENGTH.name} onChange={(value) => update('name', value)} />
        </div>
        <div>
          <FieldLabel>{t('containerTemplate.imagePullPolicy')}</FieldLabel>
          <select
            className="select"
            value={form.image_pull_policy}
            onChange={(e) => update('image_pull_policy', e.target.value)}
          >
            <option value="IfNotPresent">IfNotPresent</option>
            <option value="Always">Always</option>
            <option value="Never">Never</option>
          </select>
        </div>
        <div className="md:col-span-2">
          <FieldLabel required>{t('containerTemplate.image')}</FieldLabel>
          <LimitedTextInput value={form.image} maxLength={FIELD_MAX_LENGTH.image} onChange={(value) => update('image', value)} />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('containerTemplate.description')}</FieldLabel>
          <LimitedTextInput value={form.description} maxLength={FIELD_MAX_LENGTH.description} onChange={(value) => update('description', value)} />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('containerTemplate.ports')}</FieldLabel>
          <textarea
            className="input min-h-[4.5rem] font-mono text-[12px]"
            placeholder='[{"name":"sse","containerPort":8766}]'
            value={form.ports_json}
            onChange={(e) => update('ports_json', e.target.value)}
          />
          <div className="text-[11px] text-muted mt-1">{t('containerTemplate.portsHint')}</div>
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('containerTemplate.agentEnv')}</FieldLabel>
          <textarea
            className="input min-h-[5rem] font-mono text-[12px]"
            placeholder={'KEY=value'}
            value={form.env_text}
            onChange={(e) => update('env_text', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('containerTemplate.envFrom')}</FieldLabel>
          <textarea
            className="input min-h-[4.5rem] font-mono text-[12px]"
            placeholder='[{"secretRef":{"name":"jiuwenclaw-secret-configmap"}}]'
            value={form.env_from_json}
            onChange={(e) => update('env_from_json', e.target.value)}
          />
        </div>

        <GroupLabel>{t('containerTemplate.securityContextGroup')}</GroupLabel>
        <div>
          <label className="label">{t('containerTemplate.runAsUser')}</label>
          <input
            className="input"
            type="number"
            min={0}
            value={form.run_as_user}
            onChange={(e) => update('run_as_user', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.runAsGroup')}</label>
          <input
            className="input"
            type="number"
            min={0}
            value={form.run_as_group}
            onChange={(e) => update('run_as_group', e.target.value)}
          />
        </div>
        <div className="flex items-end gap-2 pb-1">
          <label className="label flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.privileged}
              onChange={(e) => update('privileged', e.target.checked)}
            />
            {t('containerTemplate.privileged')}
          </label>
        </div>
        <div>
          <label className="label">{t('containerTemplate.capabilitiesAdd')}</label>
          <input
            className="input"
            placeholder="SYS_ADMIN, NET_ADMIN"
            value={form.capabilities_add}
            onChange={(e) => update('capabilities_add', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.seccompType')}</label>
          <select
            className="select"
            value={form.seccomp_type}
            onChange={(e) => update('seccomp_type', e.target.value)}
          >
            <option value="">—</option>
            <option value="Unconfined">Unconfined</option>
            <option value="RuntimeDefault">RuntimeDefault</option>
          </select>
        </div>
        <div>
          <label className="label">{t('containerTemplate.apparmorType')}</label>
          <select
            className="select"
            value={form.apparmor_type}
            onChange={(e) => update('apparmor_type', e.target.value)}
          >
            <option value="">—</option>
            <option value="Unconfined">Unconfined</option>
            <option value="RuntimeDefault">RuntimeDefault</option>
          </select>
        </div>

        <GroupLabel>{t('containerTemplate.readinessProbeGroup')}</GroupLabel>
        <div>
          <label className="label">{t('containerTemplate.probeType')}</label>
          <select
            className="select"
            value={form.probe_type}
            onChange={(e) => update('probe_type', e.target.value as FormState['probe_type'])}
          >
            <option value="tcpSocket">tcpSocket</option>
            <option value="httpGet">httpGet</option>
          </select>
        </div>
        <div>
          <label className="label">{t('containerTemplate.probePort')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={65535}
            value={form.probe_port}
            onChange={(e) => update('probe_port', Number(e.target.value))}
          />
        </div>
        {form.probe_type === 'httpGet' && (
          <div>
            <label className="label">{t('containerTemplate.healthPath')}</label>
            <input
              className="input"
              value={form.probe_path}
              onChange={(e) => update('probe_path', e.target.value)}
            />
          </div>
        )}
        <div>
          <label className="label">{t('containerTemplate.readinessInitialDelay')}</label>
          <input
            className="input"
            type="number"
            min={0}
            value={form.readiness_initial_delay}
            onChange={(e) => update('readiness_initial_delay', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.readinessPeriod')}</label>
          <input
            className="input"
            type="number"
            min={1}
            value={form.readiness_period}
            onChange={(e) => update('readiness_period', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.probeTimeout')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={300}
            value={form.readiness_timeout}
            onChange={(e) => update('readiness_timeout', Number(e.target.value))}
          />
        </div>

        <div>
          <label className="label">{t('containerTemplate.cpuRequest')}</label>
          <input
            className="input"
            value={form.cpu_request}
            onChange={(e) => update('cpu_request', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.memoryRequest')}</label>
          <input
            className="input"
            value={form.memory_request}
            onChange={(e) => update('memory_request', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.cpuLimit')}</label>
          <input
            className="input"
            value={form.cpu_limit}
            onChange={(e) => update('cpu_limit', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('containerTemplate.memoryLimit')}</label>
          <input
            className="input"
            value={form.memory_limit}
            onChange={(e) => update('memory_limit', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('containerTemplate.volumeMounts')}</FieldLabel>
          <textarea
            className="input min-h-[5rem] font-mono text-[12px]"
            placeholder='[{"name":"data","mountPath":"/root/.jiuwenswarm"}]'
            value={form.volume_mounts_json}
            onChange={(e) => update('volume_mounts_json', e.target.value)}
          />
        </div>
      </div>
    </Modal>
  );
}
