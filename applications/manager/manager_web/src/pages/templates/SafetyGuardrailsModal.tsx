import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { useInvalidJsonChecker } from '../../components/JsonField';
import { useFormDirty } from '../../hooks/useFormDirty';
import { PermissionsTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  PermissionsFormState,
  PermissionsTemplate,
  PermissionsTemplateCreateBody,
  PermissionsTemplateUpdateBody,
} from '../../types';
import {
  createDefaultPermissionsFormState,
  permissionsBodyToFormState,
  permissionsFormStateToBody,
} from '../instance/instanceConfigPanel/permissionsForm';
import {
  PermissionsBodyEditor,
  validatePermissionsFormJson,
} from '../instance/instanceConfigPanel/PermissionsBodyEditor';

interface Props {
  open: boolean;
  template: PermissionsTemplate | null;
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

export function SafetyGuardrailsModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const { markClean, isDirty } = useFormDirty(open);
  const [templateName, setTemplateName] = useState('');
  const [description, setDescription] = useState('');
  const [form, setForm] = useState<PermissionsFormState>(() => createDefaultPermissionsFormState());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const nextName = template
      ? clipField(template.template_name, FIELD_MAX_LENGTH.template_name)
      : '';
    const nextDesc = template
      ? clipField(template.description ?? '', FIELD_MAX_LENGTH.description)
      : '';
    const nextForm = template
      ? permissionsBodyToFormState(template.body ?? {})
      : createDefaultPermissionsFormState();
    setTemplateName(nextName);
    setDescription(nextDesc);
    setForm(nextForm);
    markClean({ templateName: nextName, description: nextDesc, form: nextForm });
  }, [open, template, markClean]);

  const submit = async () => {
    if (!templateName.trim()) {
      toast('warn', t('safetyGuardrails.fieldRequired', { field: t('safetyGuardrails.templateName') }));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('safetyGuardrails.templateName'), value: templateName },
      { label: t('safetyGuardrails.templateDescription'), value: description },
    ]);
    if (unsafeField) {
      toast('warn', t('safetyGuardrails.unsafeText', { field: unsafeField }));
      return;
    }
    const jsonErr = validatePermissionsFormJson(form, checkJson, t);
    if (jsonErr) {
      toast('danger', jsonErr);
      return;
    }

    const body: PermissionsTemplateCreateBody | PermissionsTemplateUpdateBody = {
      template_name: templateName.trim(),
      description: description.trim() || undefined,
      body: permissionsFormStateToBody(form),
    };

    setSaving(true);
    try {
      if (template) {
        await PermissionsTemplateApi.update(template.template_id, body);
      } else {
        await PermissionsTemplateApi.create({ ...body, enabled: true } as PermissionsTemplateCreateBody);
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
      title={template ? t('safetyGuardrails.edit') : t('safetyGuardrails.new')}
      onClose={onClose}
      dirty={isDirty({ templateName, description, form })}
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
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="md:col-span-2">
            <FieldLabel required>{t('safetyGuardrails.templateName')}</FieldLabel>
            <LimitedTextInput
              value={templateName}
              maxLength={FIELD_MAX_LENGTH.template_name}
              onChange={setTemplateName}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('safetyGuardrails.templateDescription')}</FieldLabel>
            <LimitedTextInput
              value={description}
              maxLength={FIELD_MAX_LENGTH.description}
              onChange={setDescription}
            />
          </div>
        </div>
        <PermissionsBodyEditor form={form} onChange={setForm} />
      </div>
    </Modal>
  );
}
