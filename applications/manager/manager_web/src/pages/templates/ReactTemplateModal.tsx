import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ReactTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  ReactTemplate,
  ReactTemplateCreateBody,
} from '../../types';
import {
  createDefaultReactFormState,
  reactBodyToFormState,
  reactFormStateToBody,
  type ReactFormState,
} from './reactForm';
import { ReactBodyEditor, reactExtraJsonError } from './ReactBodyEditor';

interface Props {
  open: boolean;
  template: ReactTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

export function ReactTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [templateName, setTemplateName] = useState('');
  const [description, setDescription] = useState('');
  const [form, setForm] = useState<ReactFormState>(() => createDefaultReactFormState());
  const [saving, setSaving] = useState(false);
  const extraJsonError = reactExtraJsonError(form, t);

  useEffect(() => {
    if (!open) return;
    const nextName = template
      ? clipField(template.template_name, FIELD_MAX_LENGTH.template_name)
      : '';
    const nextDesc = template
      ? clipField(template.description ?? '', FIELD_MAX_LENGTH.description)
      : '';
    const nextForm = template
      ? reactBodyToFormState(template.body ?? {})
      : createDefaultReactFormState();
    setTemplateName(nextName);
    setDescription(nextDesc);
    setForm(nextForm);
    markClean({ templateName: nextName, description: nextDesc, form: nextForm });
  }, [open, template, markClean]);

  const submit = async () => {
    if (!templateName.trim()) {
      toast('warn', t('reactTemplates.fieldRequired', { field: t('reactTemplates.templateName') }));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('reactTemplates.templateName'), value: templateName },
      { label: t('reactTemplates.templateDescription'), value: description },
    ]);
    if (unsafeField) {
      toast('warn', t('reactTemplates.unsafeText', { field: unsafeField }));
      return;
    }
    if (extraJsonError) {
      toast('warn', extraJsonError);
      return;
    }

    const trimmedName = templateName.trim();
    const trimmedDescription = description.trim();
    const formBody = reactFormStateToBody(form);

    setSaving(true);
    try {
      if (template) {
        // 更新分支显式传空串以清空描述：undefined 会被 JSON.stringify 丢弃，
        // 后端按「未提交字段保持原值」处理，导致描述无法清空。
        await ReactTemplateApi.update(template.template_id, {
          template_name: trimmedName,
          description: trimmedDescription,
          body: formBody,
        });
      } else {
        await ReactTemplateApi.create({
          template_name: trimmedName,
          description: trimmedDescription || undefined,
          enabled: true,
          body: formBody,
        } as ReactTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('reactTemplates.edit') : t('reactTemplates.new')}
      onClose={onClose}
      dirty={isDirty({ templateName, description, form })}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={saving || !!extraJsonError}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <FieldLabel required>{t('reactTemplates.templateName')}</FieldLabel>
            <LimitedTextInput
              value={templateName}
              maxLength={FIELD_MAX_LENGTH.template_name}
              onChange={setTemplateName}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('reactTemplates.templateDescription')}</FieldLabel>
            <LimitedTextInput
              value={description}
              maxLength={FIELD_MAX_LENGTH.description}
              onChange={setDescription}
            />
          </div>
        </div>
        <ReactBodyEditor form={form} onChange={setForm} />
        {extraJsonError && (
          <div className="text-[11px] text-danger" role="alert">
            {extraJsonError}
          </div>
        )}
      </div>
      {template && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}