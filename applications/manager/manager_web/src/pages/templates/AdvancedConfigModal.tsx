import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Switch } from '../../components/Switch';
import { useFormDirty } from '../../hooks/useFormDirty';
import { AdvancedConfigTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  AdvancedConfigTemplate,
  AdvancedConfigTemplateCreateBody,
  AdvancedConfigTemplateUpdateBody,
} from '../../types';
import {
  ADVANCED_CONFIG_SWITCH_FIELDS,
  defaultAdvancedConfigSwitches,
  switchesFromTemplate,
  type AdvancedConfigSwitchField,
  type AdvancedConfigSwitchState,
} from './advancedConfigSwitches';

interface Props {
  open: boolean;
  template: AdvancedConfigTemplate | null;
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

export function AdvancedConfigModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [templateName, setTemplateName] = useState('');
  const [description, setDescription] = useState('');
  const [switches, setSwitches] = useState<AdvancedConfigSwitchState>(() => defaultAdvancedConfigSwitches());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const nextName = template
      ? clipField(template.template_name, FIELD_MAX_LENGTH.template_name)
      : '';
    const nextDesc = template
      ? clipField(template.description ?? '', FIELD_MAX_LENGTH.description)
      : '';
    const nextSwitches = switchesFromTemplate(template);
    setTemplateName(nextName);
    setDescription(nextDesc);
    setSwitches(nextSwitches);
    markClean({ templateName: nextName, description: nextDesc, switches: nextSwitches });
  }, [open, template, markClean]);

  const updateSwitch = (key: AdvancedConfigSwitchField, value: boolean) => {
    setSwitches((current) => ({ ...current, [key]: value }));
  };

  const field = (key: AdvancedConfigSwitchField) => t(`advancedConfigTemplates.fields.${key}`);

  const submit = async () => {
    if (!templateName.trim()) {
      toast('warn', t('advancedConfigTemplates.fieldRequired', { field: t('advancedConfigTemplates.templateName') }));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('advancedConfigTemplates.templateName'), value: templateName },
      { label: t('advancedConfigTemplates.templateDescription'), value: description },
    ]);
    if (unsafeField) {
      toast('warn', t('advancedConfigTemplates.unsafeText', { field: unsafeField }));
      return;
    }

    const body: AdvancedConfigTemplateCreateBody | AdvancedConfigTemplateUpdateBody = {
      template_name: templateName.trim(),
      description: description.trim() || null,
      ...switches,
    };

    setSaving(true);
    try {
      if (template) {
        await AdvancedConfigTemplateApi.update(template.template_id, body);
      } else {
        await AdvancedConfigTemplateApi.create({ ...body, enabled: true } as AdvancedConfigTemplateCreateBody);
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
      title={template ? t('advancedConfigTemplates.edit') : t('advancedConfigTemplates.new')}
      onClose={onClose}
      dirty={isDirty({ templateName, description, switches })}
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
            <FieldLabel required>{t('advancedConfigTemplates.templateName')}</FieldLabel>
            <LimitedTextInput
              value={templateName}
              maxLength={FIELD_MAX_LENGTH.template_name}
              onChange={setTemplateName}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('advancedConfigTemplates.templateDescription')}</FieldLabel>
            <LimitedTextInput
              value={description}
              maxLength={FIELD_MAX_LENGTH.description}
              onChange={setDescription}
            />
          </div>
        </div>

        <section className="flex flex-col gap-3">
          <div>
            <div className="text-sm font-medium text-text-strong">{t('advancedConfigTemplates.sections.switches')}</div>
            <p className="text-[11px] text-muted mt-1">{t('advancedConfigTemplates.sections.switchesHint')}</p>
          </div>
          {ADVANCED_CONFIG_SWITCH_FIELDS.map((key) => (
            <label key={key} className="flex items-center gap-2 text-xs">
              <Switch
                checked={switches[key]}
                aria-label={field(key)}
                onChange={(checked) => updateSwitch(key, checked)}
              />
              <span>{field(key)}</span>
            </label>
          ))}
        </section>
      </div>
      {template && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}