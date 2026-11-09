import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ApiError, ContainerTemplateApi, type DeploymentContainerPreview, type DeploymentContainerPreviewItem } from '../../services/api';
import { toast } from '../../stores/uiStore';
import type { ContainerTemplate, ContainerTemplateCreateBody } from '../../types';
import { findUnsafeTextField } from '../../utils/safeText';
import { containerTemplateForm, buildContainerTemplateBody, type ContainerTemplateForm } from '../../utils/containerTemplateForm';
import { ContainerTemplateFields } from './ContainerTemplateFields';
import './ServiceConfigTemplateModal.css';
import './ContainerTemplateModal.css';

interface Props {
  open: boolean;
  template: ContainerTemplate | null;
  onClose: () => void;
  onSaved: (saved: ContainerTemplate) => void;
}

type DefaultChoice = { item: DeploymentContainerPreviewItem; fingerprint: string };

export function ContainerTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState(() => containerTemplateForm());
  // Keep the full source so fields outside the compact controls survive saving.
  const [source, setSource] = useState<ContainerTemplateCreateBody | null>(null);
  const [saving, setSaving] = useState(false);
  const [loadingDefaults, setLoadingDefaults] = useState(false);
  const [defaultsError, setDefaultsError] = useState('');
  const [preview, setPreview] = useState<DeploymentContainerPreview | null>(null);
  const [selection, setSelection] = useState('');
  const [loadedDefault, setLoadedDefault] = useState<{ fingerprint: string; source_container_id: string } | null>(null);
  const [pendingDefault, setPendingDefault] = useState<DefaultChoice | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    requestVersion.current += 1;
    setLoadingDefaults(false);
    setDefaultsError('');
    setPreview(null);
    setSelection('');
    setLoadedDefault(null);
    setPendingDefault(null);
    if (!open) return;
    const next = containerTemplateForm(template);
    setSource(template);
    setForm(next);
    markClean(next);
    return () => { requestVersion.current += 1; };
  }, [open, template, markClean]);

  const update = <K extends keyof ContainerTemplateForm>(key: K, value: ContainerTemplateForm[K]) =>
    setForm(current => ({ ...current, [key]: value }));

  const applyDefault = ({ item, fingerprint }: DefaultChoice) => {
    setSource(item.template);
    setForm(containerTemplateForm(item.template));
    setLoadedDefault({ fingerprint, source_container_id: item.source_container_id });
    setSelection(item.source_container_id);
    setPendingDefault(null);
  };
  const chooseDefault = (choice: DefaultChoice) => {
    if (isDirty(form) || loadedDefault) setPendingDefault(choice);
    else applyDefault(choice);
  };
  const readDefaults = async () => {
    const version = ++requestVersion.current;
    setLoadingDefaults(true);
    setDefaultsError('');
    setPreview(null);
    setPendingDefault(null);
    try {
      const result = await ContainerTemplateApi.previewDeploymentDefaults();
      if (requestVersion.current !== version) return;
      setPreview(result);
      if (!result.fingerprint || !result.templates.length) {
        setDefaultsError(result.message || tr('暂无可读取的默认容器模板，请联系管理员检查部署配置。', 'No default containers are available. Ask an administrator to check the deployment configuration.'));
        return;
      }
      setSelection(result.templates[0].source_container_id);
      if (result.templates.length === 1) chooseDefault({ item: result.templates[0], fingerprint: result.fingerprint });
    } catch (error) {
      if (requestVersion.current === version) setDefaultsError(error instanceof ApiError ? error.detail : (error as Error).message);
    } finally {
      if (requestVersion.current === version) setLoadingDefaults(false);
    }
  };

  const submit = async () => {
    if (saving || loadingDefaults || pendingDefault) return;
    const required = [
      ['template_name', 'templateName'], ['container_id', 'containerId'],
      ['name', 'containerName'], ['image', 'image'],
    ] as const;
    for (const [key, label] of required) {
      if (!form[key].trim()) {
        toast('warn', t('containerTemplate.fieldRequired', { field: t(`containerTemplate.${label}`) }));
        return;
      }
    }
    const unsafe = findUnsafeTextField([
      ...required.map(([key, label]) => ({ label: t(`containerTemplate.${label}`), value: form[key] })),
      { label: t('containerTemplate.description'), value: form.description },
    ]);
    if (unsafe) {
      toast('warn', t('containerTemplate.unsafeText', { field: unsafe }));
      return;
    }
    let body: ContainerTemplateCreateBody;
    try {
      body = buildContainerTemplateBody(form, source);
    } catch (error) {
      toast('warn', (error as Error).message);
      return;
    }
    setSaving(true);
    try {
      const saved = template
        ? await ContainerTemplateApi.update(template.template_id, body)
        : loadedDefault
          ? await ContainerTemplateApi.restoreDeploymentDefaults({ ...loadedDefault, template: body })
          : await ContainerTemplateApi.create(body);
      toast('success', t('success.saved'));
      onSaved(saved);
    } catch (error) {
      toast('danger', t('errors.saveFailed', { detail: error instanceof ApiError ? error.detail : (error as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return <Modal open={open} title={template ? t('containerTemplate.edit') : t('containerTemplate.new')}
    onClose={onClose} dirty={isDirty(form) || !!loadedDefault} size="lg"
    footer={<><ModalCancelButton /><button className="btn primary" onClick={() => void submit()} disabled={saving || loadingDefaults || !!pendingDefault}>{saving ? t('common.loading') : t('common.save')}</button></>}
  >
    <div className="runtime-template-form container-template-form">
      {!template && <section className="runtime-template-defaults" aria-label={tr('默认容器模板', 'Default container templates')}>
        <div className="runtime-template-defaults__header">
          <div><h3>{tr('从默认配置开始', 'Start from the default configuration')}</h3><p>{tr('读取部署文件中的原始容器配置，检查后保存为新模板。', 'Read the original container configuration from the deployment file, then review and save a new template.')}</p></div>
          <button type="button" className="btn" disabled={saving || loadingDefaults} onClick={() => void readDefaults()}>{loadingDefaults ? tr('读取中…', 'Reading…') : tr('读取默认模板', 'Read default template')}</button>
        </div>
        {preview && preview.templates.length > 1 && <div className="runtime-template-defaults__selection">
          <select className="select" aria-label={tr('选择默认容器模板', 'Choose a default container template')} value={selection} disabled={saving || loadingDefaults} onChange={event => setSelection(event.target.value)}>
            {preview.templates.map(item => <option key={item.source_container_id} value={item.source_container_id}>{item.template.template_name} · {item.source_container_id}</option>)}
          </select>
          <button type="button" className="btn" disabled={saving || loadingDefaults} onClick={() => {
            const item = preview.templates.find(entry => entry.source_container_id === selection);
            if (item && preview.fingerprint) chooseDefault({ item, fingerprint: preview.fingerprint });
          }}>{tr('填入表单', 'Fill form')}</button>
        </div>}
        {defaultsError && <p className="runtime-template-defaults__message text-danger" role="alert">{defaultsError}</p>}
        {pendingDefault && <div className="runtime-template-defaults__confirm" role="group" aria-label={tr('替换当前填写内容', 'Replace current entries')}>
          <p>{tr('读取默认模板会替换当前表单内容，是否继续？', 'Loading the default template will replace your current entries. Continue?')}</p>
          <div className="runtime-template-defaults__actions">
            <button type="button" className="btn sm" onClick={() => setPendingDefault(null)}>{tr('保留当前填写', 'Keep current entries')}</button>
            <button type="button" className="btn primary sm" onClick={() => applyDefault(pendingDefault)}>{tr('替换当前填写', 'Replace entries')}</button>
          </div>
        </div>}
        {loadedDefault && !pendingDefault && <p className="runtime-template-defaults__message" role="status">{tr('已读取默认配置并生成新的容器 ID，保存后可在运行时模板中选择。', 'Defaults loaded with a new container ID. After saving, select this container in a runtime template.')}</p>}
      </section>}
      <fieldset className="runtime-template-form__fields container-template-form__fields" disabled={saving || loadingDefaults}>
        <ContainerTemplateFields form={form} update={update} />
      </fieldset>
    </div>
  </Modal>;
}
