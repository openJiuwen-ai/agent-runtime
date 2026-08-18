import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { TemplateRefEditor } from '../../components/TemplateRefEditor';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { AgentTemplate, AgentTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { fromCommaList, toCommaList } from '../../utils/format';
import {
  findSingleValueTemplateRefViolation,
  normalizeTemplateRefFromApi,
  type TemplateRefMap,
} from '../../utils/templateRef';

interface Props {
  open: boolean;
  template: AgentTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
} as const;

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  template_name: string;
  description: string;
  agent_tags: string;
  template_ref: TemplateRefMap;
}

const empty: FormState = {
  template_name: '',
  description: '',
  agent_tags: '',
  template_ref: {},
};

export function AgentTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: template.template_name.slice(0, FIELD_MAX_LENGTH.template_name),
        description: (template.description ?? '').slice(0, FIELD_MAX_LENGTH.description),
        agent_tags: toCommaList(template.agent_tags),
        template_ref: normalizeTemplateRefFromApi(template.template_ref),
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('agentTemplate.fieldRequired', { field: t('agentTemplate.templateName') }));
      return;
    }
    const singleValueViolation = findSingleValueTemplateRefViolation(form.template_ref);
    if (singleValueViolation) {
      toast('warn', t('policies.templateRef.singleValueOnly', {
        slot: t(`policies.templateRef.slots.${singleValueViolation}`, {
          defaultValue: singleValueViolation,
        }),
      }));
      return;
    }

    const body = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      agent_tags: fromCommaList(form.agent_tags),
      template_ref: form.template_ref,
    };

    setSaving(true);
    try {
      if (template) {
        await AgentTemplateApi.update(template.template_id, body);
      } else {
        await AgentTemplateApi.create(body);
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
      title={template ? t('agentTemplate.edit') : t('agentTemplate.new')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn primary" onClick={() => void submit()} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="md:col-span-2">
          <FieldLabel required>{t('agentTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('agentTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('agentTemplate.agentTags')}</FieldLabel>
          <input
            className="input"
            placeholder={t('agentTemplate.agentTagsHint')}
            value={form.agent_tags}
            onChange={(e) => update('agent_tags', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <TemplateRefEditor
            key={template?.template_id ?? 'new'}
            label={t('agentTemplate.templateRef')}
            hint={t('agentTemplate.templateRefHint')}
            value={form.template_ref}
            onChange={(v) => update('template_ref', v)}
          />
        </div>
      </div>
    </Modal>
  );
}
