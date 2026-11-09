/**
 * 运行时模板新建/编辑弹窗：基本信息、容器绑定与可折叠的高级参数。
 * 新建时可读取部署文件预览；保存后端才创建模板及所需容器副本。
 * 普通编辑保留现有绑定与元数据，容器配置在容器模板目录维护。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ContainerTemplateApi, ServiceConfigTemplateApi, ApiError, type DeploymentDefaultsPreview, type DeploymentDefaultPreviewItem } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { bumpGuideRevision } from '../../stores/guideStore';
import { ContainerTemplateModal } from './ContainerTemplateModal';
import './ServiceConfigTemplateModal.css';
import { isValidUnixAbsPath } from '../../utils/path';
import { findUnsafeTextField } from '../../utils/safeText';
import { collectPages } from '../../utils/overview';
import { runtimeTemplateForm, runtimeTemplateData, type RuntimeTemplateForm as FormState } from '../../utils/runtimeTemplateForm';
import type {
  ContainerTemplate,
  ServiceConfigContainerBrief,
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
} from '../../types';

const INT32_MAX = 2_147_483_647;

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
    <div className="runtime-template-group-label">
      <h3>{children}</h3>
      {hint && <p>{hint}</p>}
    </div>
  );
}

/** 容器模板绑定选择器 + 摘要展示（主容器 / SideCar 共用） */
function ContainerBindingSelect({
  label,
  templates,
  value,
  onChange,
}: {
  label: string;
  templates: ServiceConfigContainerBrief[];
  value: string;
  onChange: (containerId: string) => void;
}) {
  const { t } = useTranslation();
  const selected = templates.find((tpl) => tpl.container_id === value);
  return (
    <div className="runtime-template-binding">
      <select className="select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t('serviceConfigTemplate.bindingPlaceholder')}</option>
        {templates.map((tpl) => (
          <option key={tpl.container_id} value={tpl.container_id}>
            {tpl.template_name}（{tpl.container_id}）
          </option>
        ))}
      </select>
      {selected && (
        <div className="runtime-template-binding__summary">
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
  onSaved: (saved: ServiceConfigTemplate) => void;
}

export function ServiceConfigTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(() => runtimeTemplateForm());
  const [saving, setSaving] = useState(false);
  const [templateData, setTemplateData] = useState<Record<string, unknown>>();
  const [loadingDefaults, setLoadingDefaults] = useState(false);
  const [defaultsError, setDefaultsError] = useState('');
  const [defaultsPreview, setDefaultsPreview] = useState<DeploymentDefaultsPreview | null>(null);
  const [defaultSelection, setDefaultSelection] = useState('');
  const [loadedDefault, setLoadedDefault] = useState<{ fingerprint: string; source_template_id: string } | null>(null);
  const [pendingDefault, setPendingDefault] = useState<{ item: DeploymentDefaultPreviewItem; fingerprint: string } | null>(null);
  const requestVersion = useRef(0);
  /** 容器模板目录（绑定选择器数据源） */
  const [catalogContainers, setCatalogContainers] = useState<ContainerTemplate[]>([]);
  const [defaultContainers, setDefaultContainers] = useState<ServiceConfigContainerBrief[]>([]);
  const containerOptions = [...defaultContainers, ...catalogContainers.filter(item => !defaultContainers.some(draft => draft.container_id === item.container_id))];
  const [containerModalOpen, setContainerModalOpen] = useState(false);

  useEffect(() => { if (!open) setContainerModalOpen(false); }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void collectPages((page, page_size) => ContainerTemplateApi.list({ page, page_size }))
      .then((r) => {
        if (!cancelled) setCatalogContainers(r);
      })
      .catch(() => {
        if (!cancelled) setCatalogContainers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    requestVersion.current += 1;
    setLoadingDefaults(false);
    setDefaultsError('');
    setDefaultsPreview(null);
    setDefaultSelection('');
    setLoadedDefault(null);
    setDefaultContainers([]);
    setPendingDefault(null);
    if (!open) return;
    const next = runtimeTemplateForm(template ?? undefined);
    setForm(next);
    setTemplateData(runtimeTemplateData(template?.data));
    markClean(next);
    return () => { requestVersion.current += 1; };
  }, [open, template, markClean]);

  const applyDefault = (item: DeploymentDefaultPreviewItem, fingerprint: string) => {
    setForm(runtimeTemplateForm(item.template));
    setTemplateData(runtimeTemplateData(item.template.data));
    setDefaultContainers(item.containers);
    setLoadedDefault({ fingerprint, source_template_id: item.source_template_id });
    setDefaultSelection(item.source_template_id);
    setPendingDefault(null);
  };

  const chooseDefault = (item: DeploymentDefaultPreviewItem, fingerprint: string) => {
    if (isDirty(form) || loadedDefault) setPendingDefault({ item, fingerprint });
    else applyDefault(item, fingerprint);
  };

  const readDefaults = async () => {
    const version = ++requestVersion.current;
    setLoadingDefaults(true);
    setDefaultsPreview(null);
    setDefaultsError('');
    setPendingDefault(null);
    try {
      const preview = await ServiceConfigTemplateApi.previewDeploymentDefaults();
      if (requestVersion.current !== version) return;
      setDefaultsPreview(preview);
      if (!preview.fingerprint || !preview.templates.length) {
        setDefaultsError(preview.message || tr('暂无可读取的部署默认模板，请联系管理员检查部署配置。', 'No deployment defaults are available. Ask an administrator to check the deployment configuration.'));
        return;
      }
      setDefaultSelection(preview.templates[0].source_template_id);
      if (preview.templates.length === 1) chooseDefault(preview.templates[0], preview.fingerprint);
    } catch (error) {
      if (requestVersion.current === version) setDefaultsError(error instanceof ApiError ? error.detail : (error as Error).message);
    } finally {
      if (requestVersion.current === version) setLoadingDefaults(false);
    }
  };

  const defaultTemplatePicker = !template && <section className="runtime-template-defaults" aria-label={tr('部署默认模板', 'Deployment defaults')}>
    <div className="runtime-template-defaults__header">
      <div><h3>{tr('从默认配置开始', 'Start from the default configuration')}</h3><p>{tr('读取部署文件中的原始配置，填写完成后保存为新模板。', 'Read the original deployment configuration, then review and save a new template.')}</p></div>
      <button type="button" className="btn" disabled={saving || loadingDefaults} onClick={() => void readDefaults()}>{loadingDefaults ? tr('读取中…', 'Reading…') : tr('读取默认模板', 'Read default template')}</button>
    </div>
    {defaultsPreview && defaultsPreview.templates.length > 1 && <div className="runtime-template-defaults__selection">
      <select className="select" aria-label={tr('选择默认模板', 'Choose a default template')} value={defaultSelection} disabled={loadingDefaults || saving} onChange={event => setDefaultSelection(event.target.value)}>{defaultsPreview.templates.map(item => <option key={item.source_template_id} value={item.source_template_id}>{item.template.template_name}</option>)}</select>
      <button type="button" className="btn" disabled={loadingDefaults || saving} onClick={() => {
        const item = defaultsPreview.templates.find(value => value.source_template_id === defaultSelection);
        if (item && defaultsPreview.fingerprint) chooseDefault(item, defaultsPreview.fingerprint);
      }}>{tr('填入表单', 'Fill form')}</button>
    </div>}
    {defaultsError && <p className="runtime-template-defaults__message text-danger" role="alert">{defaultsError}</p>}
    {pendingDefault && <div className="runtime-template-defaults__confirm" role="group" aria-label={tr('替换当前填写内容', 'Replace current entries')}><p>{tr('读取默认模板会替换当前表单内容，是否继续？', 'Loading the default template will replace your current entries. Continue?')}</p><div className="runtime-template-defaults__actions"><button type="button" className="btn sm" onClick={() => setPendingDefault(null)}>{tr('保留当前填写', 'Keep current entries')}</button><button type="button" className="btn primary sm" onClick={() => applyDefault(pendingDefault.item, pendingDefault.fingerprint)}>{tr('替换当前填写', 'Replace entries')}</button></div></div>}
    {loadedDefault && !pendingDefault && <p className="runtime-template-defaults__message" role="status">{tr('已读取默认配置。保存时创建新模板和配套容器，现有模板与集群绑定保持不变。', 'Default configuration loaded. Saving creates a new template and its containers; existing templates and cluster bindings are preserved.')}</p>}
  </section>;

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
      namespace: form.namespace,
      description: form.description.trim(),
      node_name: form.node_name.trim(),
      fs_group: fsGroupValue,
      pod_name: form.pod_name.trim() || 'agentserver',
      sse_path: form.sse_path.trim() || '/api/v1/events/stream',
      kubeconfig: form.kubeconfig.trim(),
      ready_timeout: form.ready_timeout,
      ready_poll_interval: form.ready_poll_interval,
      main_container_id: mainId,
      sidecar_container_ids: sidecarIds,
      volumes: volumes.value ?? [],
      min_idle_pods: form.min_idle_pods,
      pod_concurrency: form.pod_concurrency,
      pod_ttl: form.pod_ttl,
      message_timeout: form.message_timeout,
      scope_concurrency: form.scope_concurrency,
      session_ttl: form.session_ttl,
      enabled: template ? template.enabled : form.enabled,
      data: templateData,
    };

    setSaving(true);
    try {
      const saved = template
        ? await ServiceConfigTemplateApi.update(template.template_id, body)
        : loadedDefault
          ? await ServiceConfigTemplateApi.restoreDeploymentDefaults({ ...loadedDefault, template: body })
          : await ServiceConfigTemplateApi.create(body);
      toast('success', t('success.saved'));
      onSaved(saved);
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
    <>
      <Modal
        open={open && !containerModalOpen}
        title={template ? t('serviceConfigTemplate.edit') : t('serviceConfigTemplate.new')}
        onClose={onClose}
        dirty={isDirty(form) || !!loadedDefault}
        size="lg"
        footer={
          <>
            <ModalCancelButton />
            <button className="btn primary" onClick={() => void submit()} disabled={saving || loadingDefaults || !!pendingDefault}>
              {saving ? t('common.loading') : t('common.save')}
            </button>
          </>
        }
      >
        <div className="runtime-template-form">
          {defaultTemplatePicker}
          <fieldset className="runtime-template-form__fields" disabled={saving || loadingDefaults}>
            <section className="runtime-template-section">
              <GroupLabel>{t('serviceConfigTemplate.basicInfo', { defaultValue: '基本信息' })}</GroupLabel>
              <div className="runtime-template-grid">
                <div className="runtime-template-field">
                  <FieldLabel required>{t('serviceConfigTemplate.templateName')}</FieldLabel>
                  <LimitedTextInput
                    ariaLabel={t('serviceConfigTemplate.templateName')}
                    value={form.template_name}
                    maxLength={128}
                    required
                    onChange={(v) => update('template_name', v)}
                  />
                </div>
                <div className="runtime-template-field">
                  <FieldLabel>{t('serviceConfigTemplate.templateDescription')}</FieldLabel>
                  <LimitedTextInput
                    ariaLabel={t('serviceConfigTemplate.templateDescription')}
                    value={form.description}
                    maxLength={512}
                    onChange={(v) => update('description', v)}
                  />
                </div>
              </div>
            </section>

            <section className="runtime-template-section">
              <div className="runtime-template-section__heading">
                <GroupLabel hint={t('serviceConfigTemplate.containerSetupHint', { defaultValue: '选择主容器，按需添加 Sandbox 等辅助容器。' })}>
                  {t('serviceConfigTemplate.containerSetup', { defaultValue: '容器配置' })}
                </GroupLabel>
                <button type="button" className="btn sm" onClick={() => setContainerModalOpen(true)}>
                  + {t('containerTemplate.new')}
                </button>
              </div>
              <div className="runtime-template-field">
                <FieldLabel required>{t('serviceConfigTemplate.mainContainerBinding')}</FieldLabel>
                <ContainerBindingSelect
                  label={t('serviceConfigTemplate.mainContainerBinding')}
                  templates={containerOptions}
                  value={form.main_container_id}
                  onChange={(containerId) => update('main_container_id', containerId)}
                />
                {form.main_container_id && !containerOptions.some((tpl) => tpl.container_id === form.main_container_id) && (
                  <p className="runtime-template-field__hint text-warning">
                    {t('serviceConfigTemplate.bindingNotFoundWarn', { id: form.main_container_id })}
                  </p>
                )}
              </div>
              <details className="runtime-template-disclosure runtime-template-disclosure--nested" open={form.sidecar_ids.length > 0 || undefined}>
                <summary>
                  <span className="runtime-template-disclosure__title">{t('serviceConfigTemplate.sidecarContainerBinding')}</span>
                  <span className="runtime-template-disclosure__meta">
                    {form.sidecar_ids.length ? t('serviceConfigTemplate.selectedContainers', { count: form.sidecar_ids.length, defaultValue: `已选 ${form.sidecar_ids.length} 个` }) : t('common.optional', { defaultValue: '可选' })}
                  </span>
                </summary>
                <div className="runtime-template-disclosure__body">
                  {form.sidecar_ids.length === 0 && <p className="runtime-template-field__hint">{t('serviceConfigTemplate.sidecarListEmpty')}</p>}
                  {form.sidecar_ids.map((id, index) => (
                    <div key={index} className="runtime-template-sidecar">
                      <div className="runtime-template-sidecar__selection">
                        <ContainerBindingSelect
                          label={`${t('serviceConfigTemplate.sidecarContainerBinding')} ${index + 1}`}
                          templates={containerOptions}
                          value={id}
                          onChange={(containerId) => update('sidecar_ids', form.sidecar_ids.map((v, i) => i === index ? containerId : v))}
                        />
                        {id && !containerOptions.some((tpl) => tpl.container_id === id) && (
                          <p className="runtime-template-field__hint text-warning">{t('serviceConfigTemplate.bindingNotFoundWarn', { id })}</p>
                        )}
                      </div>
                      <button type="button" className="btn sm danger" onClick={() => update('sidecar_ids', form.sidecar_ids.filter((_, i) => i !== index))}>
                        {t('serviceConfigTemplate.sidecarListRemove')}
                      </button>
                    </div>
                  ))}
                  <button type="button" className="btn sm runtime-template-add-sidecar" onClick={() => update('sidecar_ids', [...form.sidecar_ids, ''])}>
                    + {t('serviceConfigTemplate.sidecarListAdd')}
                  </button>
                </div>
              </details>
            </section>

            <details className="runtime-template-disclosure">
              <summary>
                <span className="runtime-template-disclosure__title">{t('redesign.capacityTimeout')}</span>
                <span className="runtime-template-disclosure__meta">{t('serviceConfigTemplate.adjustAsNeeded', { defaultValue: '按需调整' })}</span>
              </summary>
              <div className="runtime-template-disclosure__body runtime-template-grid">
                {([
                  ['min_idle_pods', 'minIdlePods', 0],
                  ['pod_concurrency', 'podConcurrency', 1],
                  ['scope_concurrency', 'scopeConcurrency', 1],
                  ['pod_ttl', 'podTtl', 1],
                  ['session_ttl', 'sessionTtl', 1],
                  ['message_timeout', 'messageTimeout', 1],
                  ['ready_timeout', 'readyTimeout', 1],
                  ['ready_poll_interval', 'readyPollInterval', 1],
                ] as const).map(([key, label, min]) => (
                  <div key={key} className="runtime-template-field">
                    <FieldLabel>{t(`serviceConfigTemplate.${label}`)}</FieldLabel>
                    <input
                      className="input"
                      aria-label={t(`serviceConfigTemplate.${label}`)}
                      type="number"
                      min={min}
                      max={INT32_MAX}
                      value={form[key]}
                      onChange={(e) => update(key, Number(e.target.value))}
                    />
                  </div>
                ))}
              </div>
            </details>

            <details className="runtime-template-disclosure">
              <summary>
                <span className="runtime-template-disclosure__title">{t('serviceConfigTemplate.deploymentStorage', { defaultValue: '部署与存储' })}</span>
                <span className="runtime-template-disclosure__meta">{t('serviceConfigTemplate.advancedParameters', { defaultValue: '高级参数' })}</span>
              </summary>
              <div className="runtime-template-disclosure__body runtime-template-grid">
                <div className="runtime-template-field">
                  <FieldLabel>{t('serviceConfigTemplate.nodeName')}</FieldLabel>
                  <LimitedTextInput ariaLabel={t('serviceConfigTemplate.nodeName')} value={form.node_name} maxLength={128} onChange={(v) => update('node_name', v)} />
                </div>
                <div className="runtime-template-field">
                  <FieldLabel>{t('serviceConfigTemplate.fsGroup')}</FieldLabel>
                  <input className="input" aria-label={t('serviceConfigTemplate.fsGroup')} type="number" min={0} max={INT32_MAX} value={form.fs_group} onChange={(e) => update('fs_group', e.target.value)} />
                </div>
                <div className="runtime-template-field">
                  <FieldLabel>{t('serviceConfigTemplate.podName')}</FieldLabel>
                  <LimitedTextInput ariaLabel={t('serviceConfigTemplate.podName')} value={form.pod_name} maxLength={128} onChange={(v) => update('pod_name', v)} />
                </div>
                <div className="runtime-template-field">
                  <FieldLabel>{t('serviceConfigTemplate.ssePath')}</FieldLabel>
                  <input className="input" aria-label={t('serviceConfigTemplate.ssePath')} value={form.sse_path} maxLength={128} onChange={(e) => update('sse_path', e.target.value)} />
                </div>
                <div className="runtime-template-field runtime-template-field--wide">
                  <FieldLabel>{t('serviceConfigTemplate.kubeconfig')}</FieldLabel>
                  <LimitedTextInput ariaLabel={t('serviceConfigTemplate.kubeconfig')} value={form.kubeconfig} maxLength={512} onChange={(v) => update('kubeconfig', v)} />
                </div>
                <div className="runtime-template-field runtime-template-field--wide">
                  <FieldLabel>{t('serviceConfigTemplate.volumes')}</FieldLabel>
                  <textarea
                    className="input runtime-template-json"
                    aria-label={t('serviceConfigTemplate.volumes')}
                    placeholder='[{"name":"data","persistentVolumeClaim":{"claimName":"jiuwenswarm-pvc"}}]'
                    value={form.volumes_json}
                    onChange={(e) => update('volumes_json', e.target.value)}
                  />
                  <p className="runtime-template-field__hint">{t('serviceConfigTemplate.volumesHint')}</p>
                </div>
              </div>
            </details>
          </fieldset>
        </div>
      </Modal>
      <ContainerTemplateModal
        open={open && containerModalOpen}
        template={null}
        onClose={() => setContainerModalOpen(false)}
        onSaved={(saved) => {
          setCatalogContainers((current) => [...current.filter((item) => item.template_id !== saved.template_id), saved]);
          if (!form.main_container_id) update('main_container_id', saved.container_id);
          setContainerModalOpen(false);
          bumpGuideRevision();
        }}
      />
    </>
  );
}
