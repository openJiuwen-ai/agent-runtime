import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { TemplateRefEditor } from '../../components/TemplateRefEditor';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { JsonField } from '../../components/JsonField';
import { useFormDirty } from '../../hooks/useFormDirty';
import { AgentTemplate, AgentTemplateApi, AgentClientRegistrationApi, ApiError } from '../../services/api';
import {
  createRegistrationId, hasAgentCredentials, mergeAgentCredentials, maskAgentClientSecrets,
  type AgentClientCredentials,
} from '../../utils/agentClientRegistration';
import { toast } from '../../stores/uiStore';
import { bumpGuideRevision } from '../../stores/guideStore';
import { fromCommaList, toCommaList } from '../../utils/format';
import { jsonContentForValidation } from '../../utils/jsonExample';
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
  custom_config_json: string;
}

const empty: FormState = {
  template_name: '',
  description: '',
  agent_tags: '',
  template_ref: {},
  custom_config_json: '',
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

export function AgentTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);
  const [registrationConfig, setRegistrationConfig] = useState({ enabled: false, configured: false, reason: '' });
  const [registering, setRegistering] = useState(false);
  const [closing, setClosing] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const registrationId = useRef('');
  const registration = useRef<AgentClientCredentials | null>(null);

  useEffect(() => {
    if (!open) return;
    const next: FormState = template
      ? {
          template_name: template.template_name.slice(0, FIELD_MAX_LENGTH.template_name),
          description: (template.description ?? '').slice(0, FIELD_MAX_LENGTH.description),
          agent_tags: toCommaList(template.agent_tags),
          template_ref: normalizeTemplateRefFromApi(template.template_ref),
          custom_config_json: customConfigText(template.data),
        }
      : empty;
    setForm(next);
    markClean(next);
    registrationId.current = '';
    registration.current = null;
    setShowSecrets(false);
    setRegistrationConfig({ enabled: false, configured: false, reason: '' });
    let active = true;
    void AgentClientRegistrationApi.capabilities().then((value) => {
      if (active) setRegistrationConfig({ ...value, reason: value.reason ?? '' });
    }).catch(() => undefined);
    return () => { active = false; };
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const customConfig = parseCustomConfig(form.custom_config_json);
  const credentialsExist = hasAgentCredentials(customConfig.value);
  const containsSecret = customConfig.value.some((entry) => typeof entry.client_secret === 'string' && entry.client_secret);
  const hideSecrets = containsSecret && !showSecrets;

  const registerClient = async () => {
    if (registering || customConfig.error || credentialsExist) return;
    setRegistering(true);
    try {
      if (!registrationId.current) registrationId.current = createRegistrationId();
      const value = await AgentClientRegistrationApi.register(registrationId.current, form.template_name.trim());
      registration.current = value;
      const merged = mergeAgentCredentials(customConfig.value, value);
      update('custom_config_json', JSON.stringify(merged, null, 2));
      setShowSecrets(false);
      toast('success', t('agentTemplate.registration.success'));
    } catch (error) {
      toast('danger', error instanceof ApiError ? error.detail : t('agentTemplate.registration.failed'));
    } finally {
      setRegistering(false);
    }
  };

  const close = async () => {
    if (registering || saving || closing) return;
    setClosing(true);
    try {
      if (registration.current) await AgentClientRegistrationApi.cancel(registration.current.registration_id);
      registration.current = null;
      onClose();
    } catch (error) {
      toast('warn', error instanceof ApiError ? error.detail : t('agentTemplate.registration.cleanupFailed'));
      onClose();
    } finally {
      setClosing(false);
    }
  };

  const submit = async () => {
    if (registering || closing || saving) return;
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

    const body = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      agent_tags: fromCommaList(form.agent_tags),
      template_ref: form.template_ref,
      data: form.custom_config_json !== customConfigText(template?.data ?? null)
        ? { ...(template?.data ?? {}), custom_config: customConfig.value,
            ...(registration.current ? { agent_client_registration: {
              registration_id: registration.current.registration_id,
              client_uuid: registration.current.client_uuid,
              client_id: registration.current.client_id,
            } } : {}), }
        : undefined,
    };

    setSaving(true);
    try {
      if (template) {
        await AgentTemplateApi.update(template.template_id, body);
      } else {
        await AgentTemplateApi.create(body);
      }
      toast('success', t('success.saved'));
      if (registration.current && !customConfig.value.some((item) => item.client_id === registration.current?.client_id)) {
        await AgentClientRegistrationApi.cancel(registration.current.registration_id).catch(() => {
          toast('warn', t('agentTemplate.registration.cleanupFailed'));
        });
      }
      registration.current = null;
      bumpGuideRevision();
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
      onClose={() => void close()}
      dirty={isDirty(form)}
      size="lg"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={() => void submit()} disabled={saving || registering || closing || customConfig.error !== null}>
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
            requiredSlots={['default_model']}
            value={form.template_ref}
            onChange={(v) => update('template_ref', v)}
          />
        </div>
        <div className="md:col-span-2" data-testid="agent-template-custom-config">
          <JsonField
            label={t('agentTemplate.customConfig')}
            value={hideSecrets ? maskAgentClientSecrets(customConfig.value) : form.custom_config_json}
            onChange={(value) => update('custom_config_json', value)}
            placeholder={'[\n  {\n    "xxx": "yyy",\n    "xxxz": "asdasd"\n  }\n]'}
            rows={7}
            readOnly={registering || hideSecrets || closing || saving}
            labelActions={<>
              {registrationConfig.enabled && <button type="button" className="btn ghost sm" onClick={() => void registerClient()}
                disabled={!registrationConfig.configured || registering || saving || closing
                  || !form.template_name.trim() || customConfig.error !== null || credentialsExist}
                title={credentialsExist ? t('agentTemplate.registration.alreadyConfigured') : registrationConfig.reason}>
                {t(registering ? 'agentTemplate.registration.pending' : 'agentTemplate.registration.button')}
              </button>}
              {containsSecret && <button type="button" className="btn ghost sm" onClick={() => setShowSecrets((value) => !value)}>
                {t(showSecrets ? 'agentTemplate.registration.hideSecret' : 'agentTemplate.registration.showSecret')}
              </button>}
            </>}
          />
          {registrationConfig.enabled && !registrationConfig.configured && <p className="text-[11px] text-danger mt-1">{registrationConfig.reason}</p>}
          <p className="text-[11px] text-muted mt-1">{t('agentTemplate.customConfigHint')}</p>
          {customConfig.error === 'objectArray' && (
            <div className="text-[11px] text-danger mt-1" role="alert">
              {t('agentTemplate.customConfigInvalid')}
            </div>
          )}
        </div>
      </div>
      {template && <ConfigRefreshCostHint scope="template" />}
    </Modal>
  );
}
