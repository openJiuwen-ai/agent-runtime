import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfigRefreshCostHint } from '../../components/ConfigRefreshCostHint';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { TemplateRefEditor } from '../../components/TemplateRefEditor';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { JsonField } from '../../components/JsonField';
import { useFormDirty } from '../../hooks/useFormDirty';
import {
  AgentTemplate, AgentTemplateApi, AgentClientRegistrationApi, ApiError,
  type AgentClientRegistrationCapabilities,
} from '../../services/api';
import { agentRegistrationErrorKey, hasAgentCredentials, maskAgentClientSecrets } from '../../utils/agentClientRegistration';
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
  registrationConfig: AgentClientRegistrationCapabilities | null;
  onClose: () => void;
  onSaved: (options?: { keepOpen: true }) => void;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
} as const;

type RegistrationErrorKey = ReturnType<typeof agentRegistrationErrorKey> | 'saveFailed';

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

export function AgentTemplateModal({ open, template, registrationConfig, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);
  const [savedTemplate, setSavedTemplate] = useState<AgentTemplate | null>(null);
  const [clientId, setClientId] = useState('');
  const [registrationError, setRegistrationError] = useState<RegistrationErrorKey | null>(null);
  const [registering, setRegistering] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const currentTemplate = savedTemplate ?? template;
  const binding = currentTemplate?.keycloak_client;
  const busy = saving || registering;

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
    setSavedTemplate(null);
    setClientId('');
    setRegistrationError(null);
    setShowSecrets(false);
  }, [open, template, markClean]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const customConfig = parseCustomConfig(form.custom_config_json);
  const credentialsExist = hasAgentCredentials(customConfig.value);
  const containsSecret = customConfig.value.some((entry) => typeof entry.client_secret === 'string' && entry.client_secret);
  const hideSecrets = containsSecret && !showSecrets;

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

    return {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      agent_tags: fromCommaList(form.agent_tags),
      template_ref: form.template_ref,
      data: form.custom_config_json !== customConfigText(currentTemplate?.data ?? null)
        ? { ...(currentTemplate?.data ?? {}), custom_config: customConfig.value }
        : undefined,
    };
  };

  const registerClient = async () => {
    const id = clientId.trim();
    if (!registrationConfig?.enabled || !registrationConfig.configured
      || busy || binding || credentialsExist || !id) return;
    const body = currentTemplate ? undefined : templateBody();
    if (!currentTemplate && !body) return;
    setRegistering(true);
    setRegistrationError(null);
    let target = currentTemplate;
    try {
      if (!target && body) {
        target = await AgentTemplateApi.create(body);
        setSavedTemplate(target);
        markClean(form);
        onSaved({ keepOpen: true });
      }
      if (!target) return;
      const result = await AgentClientRegistrationApi.register(target.template_id, id);
      setSavedTemplate({ ...target, keycloak_client: { client_id: result.client_id, status: 'bound' } });
      bumpGuideRevision();
      onSaved({ keepOpen: true });
      toast('success', t('agentTemplate.registration.success'));
    } catch (error) {
      const persisted = target ? await AgentTemplateApi.get(target.template_id).catch(() => null) : null;
      if (persisted?.keycloak_client) {
        setSavedTemplate(persisted);
        onSaved({ keepOpen: true });
      } else {
        const key = error instanceof ApiError ? agentRegistrationErrorKey(error.status, error.raw) : 'failed';
        setRegistrationError(target ? key : 'saveFailed');
      }
    } finally {
      setRegistering(false);
    }
  };

  const submit = async () => {
    if (busy) return;
    const body = templateBody();
    if (!body) return;
    setSaving(true);
    try {
      if (currentTemplate) {
        await AgentTemplateApi.update(currentTemplate.template_id, body);
      } else {
        await AgentTemplateApi.create(body);
      }
      toast('success', t('success.saved'));
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
          <FieldLabel>{t('agentTemplate.agentTags')}</FieldLabel>
          <input
            className="input"
            placeholder={t('agentTemplate.agentTagsHint')}
            value={form.agent_tags}
            onChange={(e) => update('agent_tags', e.target.value)}
          />
        </div>
        {registrationConfig?.enabled && (
          <section className="md:col-span-2 rounded-lg border border-border bg-bg-muted p-4" aria-labelledby="agent-client-title">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id="agent-client-title" className="text-sm font-semibold text-text-strong">
                {t('agentTemplate.registration.title')}
              </h3>
              <span className={binding ? 'pill ok sm' : 'pill muted sm'}>
                {t(binding ? 'agentTemplate.registration.bound' : 'agentTemplate.registration.unbound')}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">{t('agentTemplate.registration.secretHint')}</p>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <div className="min-w-[14rem] flex-1">
                <label className="label" htmlFor="agent-client-id">Client ID</label>
                <input
                  id="agent-client-id"
                  className="input font-mono"
                  value={binding?.client_id ?? clientId}
                  placeholder="customer-agent"
                  onChange={(event) => setClientId(event.target.value)}
                  readOnly={!!binding}
                  disabled={!binding && (!registrationConfig.configured || busy || credentialsExist)}
                  spellCheck={false}
                />
              </div>
              {!binding && (
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => void registerClient()}
                  disabled={!registrationConfig.configured || busy || credentialsExist || !clientId.trim()
                    || (!currentTemplate && (!form.template_name.trim() || customConfig.error !== null))}
                >
                  {t(registering ? 'agentTemplate.registration.pending'
                    : registrationError ? 'agentTemplate.registration.retry'
                    : currentTemplate ? 'agentTemplate.registration.button' : 'agentTemplate.registration.saveAndRegister')}
                </button>
              )}
            </div>
            {binding && <p className="mt-2 text-xs text-muted">{t('agentTemplate.registration.boundHint')}</p>}
            {!binding && !currentTemplate && <p className="mt-2 text-xs text-muted">{t('agentTemplate.registration.saveHint')}</p>}
            {!binding && credentialsExist && <p className="mt-2 text-xs text-muted">{t('agentTemplate.registration.alreadyConfigured')}</p>}
            {!binding && !registrationConfig.configured && <p className="mt-2 text-xs text-danger" role="alert">{t('agentTemplate.registration.unconfigured')}</p>}
            {registrationError && (
              <div className="mt-3 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger" role="alert">
                <p className="font-medium">{t(`agentTemplate.registration.${registrationError}`)}</p>
                {!template && savedTemplate && <p className="mt-1">{t('agentTemplate.registration.savedHint')}</p>}
              </div>
            )}
          </section>
        )}
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
            readOnly={busy || hideSecrets}
            labelActions={containsSecret && <button type="button" className="btn ghost sm" onClick={() => setShowSecrets((value) => !value)}>
              {t(showSecrets ? 'agentTemplate.registration.hideSecret' : 'agentTemplate.registration.showSecret')}
            </button>}
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
