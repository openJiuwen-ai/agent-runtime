import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { useEditorPresentation } from '../../components/InlineEditorContext';
import { Switch } from '../../components/Switch';
import { TemplateRefEditor } from '../../components/TemplateRefEditor';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { JsonField } from '../../components/JsonField';
import { useFormDirty } from '../../hooks/useFormDirty';
import { AgentTemplate, AgentTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { bumpGuideRevision } from '../../stores/guideStore';
import { jsonContentForValidation } from '../../utils/jsonExample';
import {
  findSingleValueTemplateRefViolation,
  normalizeTemplateRefFromApi,
  type TemplateRefMap,
} from '../../utils/templateRef';

interface Props {
  open: boolean;
  template: AgentTemplate | null;
  copyFrom?: AgentTemplate;
  onClose: () => void;
  onSaved: (saved: AgentTemplate) => void;
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
  template_ref: TemplateRefMap;
  custom_config_json: string;
  upload_local_skill: boolean;
}

const empty: FormState = {
  template_name: '',
  description: '',
  template_ref: {},
  custom_config_json: '',
  upload_local_skill: false,
};

function customConfigText(data: AgentTemplate['data']): string {
  const value = data?.custom_config;
  return value === undefined ? '' : JSON.stringify(value, null, 2);
}

function parseCustomConfig(text: string): {
  value: Record<string, unknown>[];
  error: 'json' | 'objectArray' | null;
} {
  const content = jsonContentForValidation(text);
  if (!content) return { value: [], error: null };
  try {
    const value: unknown = JSON.parse(content);
    if (!Array.isArray(value) || value.some((item) => (
      item === null || typeof item !== 'object' || Array.isArray(item)
    ))) {
      return { value: [], error: 'objectArray' };
    }
    return { value, error: null };
  } catch {
    return { value: [], error: 'json' };
  }
}

export function AgentTemplateModal({ open, template, copyFrom, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);
  const presentation = useEditorPresentation();
  const sourceTemplate = template ?? copyFrom;
  const currentTemplate = template;
  const busy = saving;

  useEffect(() => {
    if (!open) return;
    const next: FormState = sourceTemplate
      ? {
          template_name: sourceTemplate.template_name.slice(0, FIELD_MAX_LENGTH.template_name),
          description: (sourceTemplate.description ?? '').slice(0, FIELD_MAX_LENGTH.description),
          template_ref: normalizeTemplateRefFromApi(sourceTemplate.template_ref),
          custom_config_json: customConfigText(sourceTemplate.data),
          upload_local_skill: sourceTemplate.data?.upload_local_skill === true,
        }
      : empty;
    if (!template && copyFrom) next.template_name = `${next.template_name.slice(0, 117)} - copy`;
    setForm(next);
    markClean(next);
  }, [open, template, copyFrom, markClean]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const customConfig = parseCustomConfig(form.custom_config_json);
  const templateBody = () => {
    if (customConfig.error) {
      toast('warn', t('agentTemplate.customConfigInvalid'));
      return;
    }
    if (!form.template_name.trim()) {
      toast('warn', t('agentTemplate.fieldRequired', { field: t('agentTemplate.templateName') }));
      return;
    }
    const defaultModelRefs = form.template_ref.default_model;
    if (!defaultModelRefs?.length || defaultModelRefs.every((ref) => !ref.trim())) {
      toast('warn', t('agentTemplate.fieldRequired', {
        field: t('policies.templateRef.slots.default_model', { defaultValue: 'default_model' }),
      }));
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

    const nextData: Record<string, unknown> = {
      ...(sourceTemplate?.data ?? {}),
    };
    if (form.custom_config_json !== customConfigText(sourceTemplate?.data ?? null)) {
      nextData.custom_config = customConfig.value;
    }
    nextData.upload_local_skill = form.upload_local_skill;

    return {
      template_name: form.template_name.trim(),
      description: form.description.trim() || null,
      template_ref: form.template_ref,
      data: nextData,
    };
  };

  const submit = async () => {
    if (busy) return;
    const body = templateBody();
    if (!body) return;
    setSaving(true);
    try {
      const saved = currentTemplate
        ? await AgentTemplateApi.update(currentTemplate.template_id, body)
        : await AgentTemplateApi.create(body);
      toast('success', t('success.saved'));
      bumpGuideRevision();
      markClean(form);
      onSaved(saved);
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={currentTemplate ? t('agentTemplate.edit') : t('agentTemplate.new')}
      onClose={() => { if (!busy) onClose(); }}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={busy || customConfig.error !== null}>
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
          <TemplateRefEditor
            key={template?.template_id ?? 'new'}
            resourceLinksInNewTab
            collapseOptional
            expandOptional={presentation.readOnly || !!customConfig.error}
            label={t('agentTemplate.templateRef')}
            hint={t('agentTemplate.templateRefHint')}
            requiredSlots={['default_model']}
            value={form.template_ref}
            onChange={(v) => update('template_ref', v)}
            optionalSelectedCount={form.upload_local_skill ? 1 : 0}
            optionalContent={(
              <section className="agent-capability-card" data-testid="agent-template-upload-local-skill">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div className="label mb-0">{t('agentTemplate.uploadLocalSkill')}</div>
                    <p className="mt-1 text-xs text-muted">{t('agentTemplate.uploadLocalSkillHint')}</p>
                  </div>
                  <Switch
                    checked={form.upload_local_skill}
                    onChange={(value) => update('upload_local_skill', value)}
                    disabled={busy}
                    aria-label={t('agentTemplate.uploadLocalSkill')}
                  />
                </div>
              </section>
            )}
          />
        </div>
        <div className="md:col-span-2" data-testid="agent-template-custom-config">
          <JsonField
            label={t('agentTemplate.customConfig')}
            value={form.custom_config_json}
            onChange={(value) => update('custom_config_json', value)}
            placeholder={'[\n  {\n    "xxx": "yyy",\n    "xxxz": "asdasd"\n  }\n]'}
            rows={7}
            readOnly={busy}
          />
          <p className="text-[11px] text-muted mt-1">{t('agentTemplate.customConfigHint')}</p>
          {customConfig.error === 'objectArray' && (
            <div className="text-[11px] text-danger mt-1" role="alert">
              {t('agentTemplate.customConfigInvalid')}
            </div>
          )}
        </div>
      </div>
      {currentTemplate && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}
