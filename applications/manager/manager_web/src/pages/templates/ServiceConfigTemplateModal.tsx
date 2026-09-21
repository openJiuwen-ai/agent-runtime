/**
 * 运行时模板新建/编辑弹窗：实例池参数 + 主容器模板绑定（必选）+ SideCar 容器模板列表。
 * 取代原三页签编辑页；容器规格仍统一在「运行时资源 → 容器模板」维护，
 * 本弹窗仅维护绑定关系；保存时写 main_container_id / sidecar_container_ids，
 * data.config_sync（scopes / source_template_id）原样保留。
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ContainerTemplateApi, ServiceConfigTemplateApi, ApiError } from '../../services/api';
import { useRouter } from '../../router';
import { toast } from '../../stores/uiStore';
import { isValidUnixAbsPath } from '../../utils/path';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  ContainerTemplate,
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
} from '../../types';

const INT32_MAX = 2_147_483_647;

type FormState = {
  template_name: string;
  description: string;
  node_name: string;
  fs_group: string;
  pod_name: string;
  sse_path: string;
  kubeconfig: string;
  ready_timeout: number;
  ready_poll_interval: number;
  min_idle_pods: number;
  pod_concurrency: number;
  pod_ttl: number;
  message_timeout: number;
  scope_concurrency: number;
  session_ttl: number;
  volumes_json: string;
  main_container_id: string;
  /** SideCar 容器模板列表：每行一个 container_id，可增删 */
  sidecar_ids: string[];
};

const emptyForm = (): FormState => ({
  template_name: '',
  description: '',
  node_name: '',
  fs_group: '',
  pod_name: 'agentserver',
  sse_path: '/api/v1/events/stream',
  kubeconfig: '',
  ready_timeout: 300,
  ready_poll_interval: 2,
  min_idle_pods: 0,
  pod_concurrency: 2,
  pod_ttl: 300,
  message_timeout: 600,
  scope_concurrency: 3,
  session_ttl: 60,
  volumes_json: '',
  main_container_id: '',
  sidecar_ids: [],
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

function GroupLabel({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="md:col-span-2 mt-2 border-t pt-3 first:mt-0 first:border-t-0 first:pt-0">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-muted">{children}</div>
      {hint && <div className="text-[11px] text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

/** 容器模板绑定选择器 + 摘要展示（主容器 / SideCar 共用） */
function ContainerBindingSelect({
  templates,
  value,
  onChange,
}: {
  templates: ContainerTemplate[];
  value: string;
  onChange: (containerId: string) => void;
}) {
  const { t } = useTranslation();
  const selected = templates.find((tpl) => tpl.container_id === value);
  return (
    <div className="flex flex-col gap-2">
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t('serviceConfigTemplate.bindingPlaceholder')}</option>
        {templates.map((tpl) => (
          <option key={tpl.container_id} value={tpl.container_id}>
            {tpl.template_name}（{tpl.container_id}）
          </option>
        ))}
      </select>
      {selected && (
        <div className="rounded border border-[var(--border)] bg-[var(--bg-muted)] px-3 py-2 text-[12px]">
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span>
              <span className="text-muted">{t('serviceConfigTemplate.bindingSummaryContainerId')}：</span>
              <span className="mono">{selected.container_id}</span>
            </span>
            <span>
              <span className="text-muted">{t('serviceConfigTemplate.bindingSummaryImage')}：</span>
              <span className="mono break-all">{selected.image || '—'}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

interface Props {
  open: boolean;
  /** 编辑目标；null = 新建 */
  template: ServiceConfigTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ServiceConfigTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  /** data.config_sync 透传字段（不可编辑，保存时原样保留） */
  const [scopes, setScopes] = useState<Record<string, unknown>[]>([]);
  const [sourceTemplateId, setSourceTemplateId] = useState<string | undefined>();
  /** 容器模板目录（绑定选择器数据源） */
  const [containerOptions, setContainerOptions] = useState<ContainerTemplate[]>([]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void ContainerTemplateApi.list({ page: 1, page_size: 200 })
      .then((r) => {
        if (!cancelled) setContainerOptions(r.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setContainerOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (template) {
      const sync = (template.data?.config_sync as Record<string, unknown> | undefined) ?? {};
      const next: FormState = {
        template_name: template.template_name,
        description: template.description ?? '',
        node_name: template.node_name ?? '',
        fs_group: template.fs_group != null ? String(template.fs_group) : '',
        pod_name: template.pod_name || 'agentserver',
        sse_path: template.sse_path || '/api/v1/events/stream',
        kubeconfig: template.kubeconfig ?? '',
        ready_timeout: template.ready_timeout,
        ready_poll_interval: template.ready_poll_interval,
        min_idle_pods: template.min_idle_pods,
        pod_concurrency: template.pod_concurrency,
        pod_ttl: template.pod_ttl,
        message_timeout: template.message_timeout,
        scope_concurrency: template.scope_concurrency,
        session_ttl: template.session_ttl,
        volumes_json: formatJson(template.volumes),
        main_container_id: template.main_container_id || '',
        sidecar_ids: [...(template.sidecar_container_ids ?? [])],
      };
      setForm(next);
      setScopes(Array.isArray(sync.scopes) ? (sync.scopes as Record<string, unknown>[]) : []);
      setSourceTemplateId(
        typeof sync.source_template_id === 'string' ? sync.source_template_id : undefined,
      );
      markClean(next);
      return;
    }
    const next = emptyForm();
    setForm(next);
    setScopes([]);
    setSourceTemplateId(undefined);
    markClean(next);
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: t('serviceConfigTemplate.templateName') }));
      return;
    }
    if (!form.main_container_id.trim()) {
      toast('warn', t('serviceConfigTemplate.bindingMissingWarn', { role: t('serviceConfigTemplate.mainContainerBinding') }));
      return;
    }
    if (form.sidecar_ids.some((id) => !id.trim())) {
      toast('warn', t('serviceConfigTemplate.sidecarRowMissing'));
      return;
    }
    const mainId = form.main_container_id.trim();
    const sidecarIds = form.sidecar_ids.map((id) => id.trim());
    if (new Set(sidecarIds).size !== sidecarIds.length || sidecarIds.includes(mainId)) {
      toast('warn', t('serviceConfigTemplate.containerIdConflict'));
      return;
    }
    if (form.sse_path.trim() && !isValidUnixAbsPath(form.sse_path.trim())) {
      toast('warn', t('serviceConfigTemplate.pathInvalid', { field: t('serviceConfigTemplate.ssePath') }));
      return;
    }
    const volumes = parseJsonArray(form.volumes_json);
    if (!volumes.ok) {
      toast('warn', t('serviceConfigTemplate.jsonArrayInvalid', { field: t('serviceConfigTemplate.volumes') }));
      return;
    }
    const unsafe = findUnsafeTextField([
      { label: t('serviceConfigTemplate.templateName'), value: form.template_name },
      { label: t('serviceConfigTemplate.templateDescription'), value: form.description },
      { label: t('serviceConfigTemplate.podName'), value: form.pod_name },
    ]);
    if (unsafe) {
      toast('warn', t('serviceConfigTemplate.unsafeText', { field: unsafe }));
      return;
    }

    const fsGroup = Number(form.fs_group);
    const fsGroupValue =
      form.fs_group.trim() && Number.isInteger(fsGroup) && fsGroup >= 0 && fsGroup <= INT32_MAX
        ? fsGroup
        : null;

    const body: ServiceConfigTemplateCreateBody = {
      template_name: form.template_name.trim(),
      description: opt(form.description),
      node_name: opt(form.node_name),
      fs_group: fsGroupValue,
      pod_name: form.pod_name.trim() || 'agentserver',
      sse_path: form.sse_path.trim() || '/api/v1/events/stream',
      kubeconfig: opt(form.kubeconfig),
      ready_timeout: form.ready_timeout,
      ready_poll_interval: form.ready_poll_interval,
      main_container_id: mainId,
      sidecar_container_ids: sidecarIds,
      volumes: volumes.value,
      min_idle_pods: form.min_idle_pods,
      pod_concurrency: form.pod_concurrency,
      pod_ttl: form.pod_ttl,
      message_timeout: form.message_timeout,
      scope_concurrency: form.scope_concurrency,
      session_ttl: form.session_ttl,
      enabled: template ? template.enabled : true,
      data: {
        config_sync: {
          scopes,
          ...(sourceTemplateId ? { source_template_id: sourceTemplateId } : {}),
        },
      },
    };

    setSaving(true);
    try {
      if (template) {
        await ServiceConfigTemplateApi.update(template.template_id, body);
      } else {
        await ServiceConfigTemplateApi.create(body);
      }
      toast('success', t('success.saved'));
      onSaved();
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

  return (
    <Modal
      open={open}
      title={template ? t('serviceConfigTemplate.edit') : t('serviceConfigTemplate.new')}
      onClose={onClose}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <GroupLabel>{t('serviceConfigTemplate.tabTemplate')}</GroupLabel>
        <div className="md:col-span-2">
          <FieldLabel required>{t('serviceConfigTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={128}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('serviceConfigTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={512}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div>
          <FieldLabel>{t('serviceConfigTemplate.nodeName')}</FieldLabel>
          <LimitedTextInput
            value={form.node_name}
            maxLength={128}
            onChange={(v) => update('node_name', v)}
          />
        </div>
        <div>
          <FieldLabel>{t('serviceConfigTemplate.fsGroup')}</FieldLabel>
          <input
            className="input"
            type="number"
            min={0}
            max={INT32_MAX}
            value={form.fs_group}
            onChange={(e) => update('fs_group', e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>{t('serviceConfigTemplate.podName')}</FieldLabel>
          <LimitedTextInput
            value={form.pod_name}
            maxLength={128}
            onChange={(v) => update('pod_name', v)}
          />
        </div>
        <div>
          <FieldLabel>{t('serviceConfigTemplate.ssePath')}</FieldLabel>
          <input
            className="input"
            value={form.sse_path}
            maxLength={128}
            onChange={(e) => update('sse_path', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('serviceConfigTemplate.kubeconfig')}</FieldLabel>
          <LimitedTextInput
            value={form.kubeconfig}
            maxLength={512}
            onChange={(v) => update('kubeconfig', v)}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.readyTimeout')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.ready_timeout}
            onChange={(e) => update('ready_timeout', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.readyPollInterval')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.ready_poll_interval}
            onChange={(e) => update('ready_poll_interval', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.minIdlePods')}</label>
          <input
            className="input"
            type="number"
            min={0}
            max={INT32_MAX}
            value={form.min_idle_pods}
            onChange={(e) => update('min_idle_pods', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.podConcurrency')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.pod_concurrency}
            onChange={(e) => update('pod_concurrency', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.podTtl')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.pod_ttl}
            onChange={(e) => update('pod_ttl', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.scopeConcurrency')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.scope_concurrency}
            onChange={(e) => update('scope_concurrency', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.sessionTtl')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.session_ttl}
            onChange={(e) => update('session_ttl', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">{t('serviceConfigTemplate.messageTimeout')}</label>
          <input
            className="input"
            type="number"
            min={1}
            max={INT32_MAX}
            value={form.message_timeout}
            onChange={(e) => update('message_timeout', Number(e.target.value))}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('serviceConfigTemplate.volumes')}</label>
          <textarea
            className="input min-h-[8rem] font-mono text-[12px]"
            placeholder='[{"name":"data","persistentVolumeClaim":{"claimName":"jiuwenclaw-pvc"}}]'
            value={form.volumes_json}
            onChange={(e) => update('volumes_json', e.target.value)}
          />
          <div className="text-[11px] text-muted mt-1">{t('serviceConfigTemplate.volumesHint')}</div>
        </div>

        <GroupLabel hint={t('serviceConfigTemplate.mainContainerBindingHint')}>
          {t('serviceConfigTemplate.mainContainerBinding')}
        </GroupLabel>
        <div className="md:col-span-2 flex flex-col gap-2">
          <ContainerBindingSelect
            templates={containerOptions}
            value={form.main_container_id}
            onChange={(containerId) => update('main_container_id', containerId)}
          />
          {form.main_container_id &&
            !containerOptions.some((tpl) => tpl.container_id === form.main_container_id) && (
              <div className="text-[11px] text-warning">
                {t('serviceConfigTemplate.bindingNotFoundWarn', { id: form.main_container_id })}
              </div>
            )}
          <div>
            <button type="button" className="btn ghost sm" onClick={() => navigate('/container-templates')}>
              {t('serviceConfigTemplate.goCreateContainerTemplate')}
            </button>
          </div>
        </div>

        <GroupLabel hint={t('serviceConfigTemplate.sidecarContainerBindingHint')}>
          {t('serviceConfigTemplate.sidecarContainerBinding')}
        </GroupLabel>
        <div className="md:col-span-2 flex flex-col gap-2">
          {form.sidecar_ids.length === 0 && (
            <div className="text-[11px] text-muted">{t('serviceConfigTemplate.sidecarListEmpty')}</div>
          )}
          {form.sidecar_ids.map((id, index) => (
            <div key={index} className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <select
                  className="select"
                  value={id}
                  onChange={(e) =>
                    update(
                      'sidecar_ids',
                      form.sidecar_ids.map((v, i) => (i === index ? e.target.value : v)),
                    )
                  }
                >
                  <option value="">{t('serviceConfigTemplate.bindingPlaceholder')}</option>
                  {containerOptions.map((tpl) => (
                    <option key={tpl.container_id} value={tpl.container_id}>
                      {tpl.template_name}（{tpl.container_id}）
                    </option>
                  ))}
                </select>
                {id && !containerOptions.some((tpl) => tpl.container_id === id) && (
                  <div className="mt-1 text-[11px] text-warning">
                    {t('serviceConfigTemplate.bindingNotFoundWarn', { id })}
                  </div>
                )}
              </div>
              <button
                type="button"
                className="btn sm danger shrink-0"
                onClick={() =>
                  update(
                    'sidecar_ids',
                    form.sidecar_ids.filter((_, i) => i !== index),
                  )
                }
              >
                {t('serviceConfigTemplate.sidecarListRemove')}
              </button>
            </div>
          ))}
          <div>
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => update('sidecar_ids', [...form.sidecar_ids, ''])}
            >
              + {t('serviceConfigTemplate.sidecarListAdd')}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
