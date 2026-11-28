import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { Switch } from '../../components/Switch';
import { useFormDirty } from '../../hooks/useFormDirty';
import { ExecutionGuardTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  ExecutionGuardTemplate,
  ExecutionGuardTemplateCreateBody,
  ExecutionGuardTemplateUpdateBody,
} from '../../types';
import {
  createDefaultExecutionGuardFormState,
  executionGuardBodyToFormState,
  executionGuardFormStateToBody,
  validateExecutionGuardForm,
  type ExecutionGuardFormError,
  type ExecutionGuardFormState,
} from './executionGuardForm';

interface Props {
  open: boolean;
  template: ExecutionGuardTemplate | null;
  copyFrom?: ExecutionGuardTemplate;
  onClose: () => void;
  onSaved: (saved: ExecutionGuardTemplate) => void;
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

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <FieldLabel required>{label}</FieldLabel>
      <input
        className="input"
        type="number"
        value={Number.isFinite(value) ? value : ''}
        onChange={(event) => {
          const raw = event.target.value;
          onChange(raw === '' ? Number.NaN : Number(raw));
        }}
      />
    </div>
  );
}

export function ExecutionGuardrailsModal({ open, template, copyFrom, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [templateName, setTemplateName] = useState('');
  const [description, setDescription] = useState('');
  const [form, setForm] = useState<ExecutionGuardFormState>(() => createDefaultExecutionGuardFormState());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    const sourceTemplate = template ?? copyFrom;
    const sourceName = sourceTemplate
      ? clipField(sourceTemplate.template_name, FIELD_MAX_LENGTH.template_name)
      : '';
    const nextDesc = sourceTemplate
      ? clipField(sourceTemplate.description ?? '', FIELD_MAX_LENGTH.description)
      : '';
    const nextForm = sourceTemplate
      ? executionGuardBodyToFormState(sourceTemplate.body ?? {})
      : createDefaultExecutionGuardFormState();
    const nextName = !template && copyFrom ? `${sourceName.slice(0, 117)} - copy` : sourceName;
    setTemplateName(nextName);
    setDescription(nextDesc);
    setForm(nextForm);
    markClean({ templateName: nextName, description: nextDesc, form: nextForm });
  }, [open, template, copyFrom, markClean]);

  const update = <K extends keyof ExecutionGuardFormState>(key: K, value: ExecutionGuardFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const showError = (error: ExecutionGuardFormError) => {
    if (error.code === 'notInteger' || error.code === 'belowMinimum') {
      const field = t(`executionGuardrails.fields.${error.field}`);
      toast('warn', error.code === 'notInteger'
        ? t('executionGuardrails.notInteger', { field })
        : t('executionGuardrails.belowMinimum', { field, minimum: error.minimum }));
      return;
    }
    if (error.code === 'repeatMax') {
      toast('warn', t('executionGuardrails.repeatMaxMin'));
      return;
    }
    if (error.code === 'window') {
      toast('warn', t('executionGuardrails.windowMin', { minimum: error.minimum }));
      return;
    }
    toast('warn', t('executionGuardrails.criticalMin'));
  };

  const submit = async () => {
    if (!templateName.trim()) {
      toast('warn', t('executionGuardrails.fieldRequired', { field: t('executionGuardrails.templateName') }));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('executionGuardrails.templateName'), value: templateName },
      { label: t('executionGuardrails.templateDescription'), value: description },
    ]);
    if (unsafeField) {
      toast('warn', t('executionGuardrails.unsafeText', { field: unsafeField }));
      return;
    }
    const formError = validateExecutionGuardForm(form);
    if (formError) {
      showError(formError);
      return;
    }

    let guardBody = executionGuardFormStateToBody(form);
    if (!template && copyFrom) {
      const baseline = executionGuardFormStateToBody(executionGuardBodyToFormState(copyFrom.body));
      const mergeEdits = (
        original: Record<string, unknown>,
        normalized: Record<string, unknown>,
        edited: Record<string, unknown>,
      ): Record<string, unknown> => {
        const result = { ...original };
        for (const [key, value] of Object.entries(edited)) {
          if (JSON.stringify(value) === JSON.stringify(normalized[key])) continue;
          const previous = normalized[key];
          const source = original[key];
          if (value && previous && source && typeof value === 'object' && typeof previous === 'object'
            && typeof source === 'object' && !Array.isArray(value) && !Array.isArray(previous) && !Array.isArray(source)) {
            result[key] = mergeEdits(source as Record<string, unknown>, previous as Record<string, unknown>, value as Record<string, unknown>);
          } else result[key] = value;
        }
        return result;
      };
      guardBody = mergeEdits(copyFrom.body, baseline, guardBody);
    }
    const body: ExecutionGuardTemplateCreateBody | ExecutionGuardTemplateUpdateBody = {
      template_name: templateName.trim(),
      description: description.trim() || null,
      body: guardBody,
    };

    setSaving(true);
    try {
      const saved = template
        ? await ExecutionGuardTemplateApi.update(template.template_id, body)
        : await ExecutionGuardTemplateApi.create({ data: copyFrom?.data ?? undefined, ...body, enabled: true } as ExecutionGuardTemplateCreateBody);
      toast('success', t('success.saved'));
      onSaved(saved);
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const field = (key: keyof ExecutionGuardFormState) => t(`executionGuardrails.fields.${key}`);

  return (
    <Modal
      open={open}
      title={template ? t('executionGuardrails.edit') : t('executionGuardrails.new')}
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
            <FieldLabel required>{t('executionGuardrails.templateName')}</FieldLabel>
            <LimitedTextInput
              value={templateName}
              maxLength={FIELD_MAX_LENGTH.template_name}
              onChange={setTemplateName}
            />
          </div>
          <div className="md:col-span-2">
            <FieldLabel>{t('executionGuardrails.templateDescription')}</FieldLabel>
            <LimitedTextInput
              value={description}
              maxLength={FIELD_MAX_LENGTH.description}
              onChange={setDescription}
            />
          </div>
        </div>

        <section className="flex flex-col gap-3">
          <div>
            <div className="text-sm font-medium text-text-strong">{t('executionGuardrails.sections.anomaly')}</div>
            <p className="text-[11px] text-muted mt-1">{t('executionGuardrails.sections.anomalyHint')}</p>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <Switch
              checked={form.anomalyEnabled}
              aria-label={field('anomalyEnabled')}
              onChange={(checked) => update('anomalyEnabled', checked)}
            />
            <span>{field('anomalyEnabled')}</span>
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <NumberField label={field('maxRetries')} value={form.maxRetries} onChange={(value) => update('maxRetries', value)} />
            <NumberField label={field('transientMaxRetries')} value={form.transientMaxRetries} onChange={(value) => update('transientMaxRetries', value)} />
            <NumberField label={field('repeatMinPatternChars')} value={form.repeatMinPatternChars} onChange={(value) => update('repeatMinPatternChars', value)} />
            <NumberField label={field('repeatMaxPatternChars')} value={form.repeatMaxPatternChars} onChange={(value) => update('repeatMaxPatternChars', value)} />
            <NumberField label={field('repeatMinCount')} value={form.repeatMinCount} onChange={(value) => update('repeatMinCount', value)} />
            <NumberField label={field('repeatMinTotalChars')} value={form.repeatMinTotalChars} onChange={(value) => update('repeatMinTotalChars', value)} />
            <NumberField label={field('repeatWindowChars')} value={form.repeatWindowChars} onChange={(value) => update('repeatWindowChars', value)} />
            <NumberField label={field('singleCharRepeatCount')} value={form.singleCharRepeatCount} onChange={(value) => update('singleCharRepeatCount', value)} />
          </div>
          <div className="border border-border rounded-md p-3 flex flex-col gap-3">
            <div>
              <div className="text-sm font-medium">{t('executionGuardrails.sections.compact')}</div>
              <p className="text-[11px] text-muted mt-1">{t('executionGuardrails.sections.compactHint')}</p>
            </div>
            <label className="flex items-center gap-2 text-xs">
              <Switch
                checked={form.compactEnabled}
                aria-label={field('compactEnabled')}
                onChange={(checked) => update('compactEnabled', checked)}
              />
              <span>{field('compactEnabled')}</span>
            </label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <NumberField label={field('consecutiveThreshold')} value={form.consecutiveThreshold} onChange={(value) => update('consecutiveThreshold', value)} />
              <NumberField label={field('bailoutThreshold')} value={form.bailoutThreshold} onChange={(value) => update('bailoutThreshold', value)} />
            </div>
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <div>
            <div className="text-sm font-medium text-text-strong">{t('executionGuardrails.sections.breaker')}</div>
            <p className="text-[11px] text-muted mt-1">{t('executionGuardrails.sections.breakerHint')}</p>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <Switch
              checked={form.breakerEnabled}
              aria-label={field('breakerEnabled')}
              onChange={(checked) => update('breakerEnabled', checked)}
            />
            <span>{field('breakerEnabled')}</span>
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <NumberField label={field('warningThreshold')} value={form.warningThreshold} onChange={(value) => update('warningThreshold', value)} />
            <NumberField label={field('criticalThreshold')} value={form.criticalThreshold} onChange={(value) => update('criticalThreshold', value)} />
            <NumberField label={field('globalBreakerThreshold')} value={form.globalBreakerThreshold} onChange={(value) => update('globalBreakerThreshold', value)} />
            <NumberField label={field('unknownToolThreshold')} value={form.unknownToolThreshold} onChange={(value) => update('unknownToolThreshold', value)} />
          </div>
        </section>
      </div>
      {template && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}
