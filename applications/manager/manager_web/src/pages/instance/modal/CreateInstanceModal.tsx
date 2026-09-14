import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../../components/Modal';
import { LimitedTextInput } from '../../../components/LimitedTextInput';
import { useFormDirty } from '../../../hooks/useFormDirty';
import { InstanceApi, ApiError } from '../../../services/api';
import { toast } from '../../../stores/uiStore';
import { findUnsafeTextField } from '../../../utils/safeText';
import { isValidHttpUrl } from '../../../utils/url';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const DEFAULT_GATEWAY_CONFIG_HOST = 'http://jiuwenclaw-gateway:8775';
const DEFAULT_RUNTIME_CONFIG_HOST = 'http://jiuwenclaw-agent-runtime:8091';
const DEFAULT_USER_WEB_HOST = 'http://jiuwenclaw-web:5173';
const DEFAULT_GATEWAY_WEB_HTTP_HOST = 'http://jiuwenclaw-gateway:19002';
const DEFAULT_GATEWAY_WEB_WS_HOST = 'http://jiuwenclaw-gateway:19000';

/** 与 instance_info 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  jiuwenclaw_name: 128,
  description: 4096,
  gateway_config_host: 512,
  runtime_config_host: 512,
  user_web_host: 512,
  gateway_web_http_host: 512,
  gateway_web_ws_host: 512,
} as const;

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

export function CreateInstanceModal({ open, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [gatewayConfigHost, setGatewayConfigHost] = useState(DEFAULT_GATEWAY_CONFIG_HOST);
  const [runtimeConfigHost, setRuntimeConfigHost] = useState(DEFAULT_RUNTIME_CONFIG_HOST);
  const [userWebHost, setUserWebHost] = useState(DEFAULT_USER_WEB_HOST);
  const [gatewayWebHttpHost, setGatewayWebHttpHost] = useState(DEFAULT_GATEWAY_WEB_HTTP_HOST);
  const [gatewayWebWsHost, setGatewayWebWsHost] = useState(DEFAULT_GATEWAY_WEB_WS_HOST);
  const [saving, setSaving] = useState(false);

  const draft = {
    name,
    description,
    gatewayConfigHost,
    runtimeConfigHost,
    userWebHost,
    gatewayWebHttpHost,
    gatewayWebWsHost,
  };

  const applyDefaults = () => {
    const next = {
      name: '',
      description: '',
      gatewayConfigHost: DEFAULT_GATEWAY_CONFIG_HOST,
      runtimeConfigHost: DEFAULT_RUNTIME_CONFIG_HOST,
      userWebHost: DEFAULT_USER_WEB_HOST,
      gatewayWebHttpHost: DEFAULT_GATEWAY_WEB_HTTP_HOST,
      gatewayWebWsHost: DEFAULT_GATEWAY_WEB_WS_HOST,
    };
    setName(next.name);
    setDescription(next.description);
    setGatewayConfigHost(next.gatewayConfigHost);
    setRuntimeConfigHost(next.runtimeConfigHost);
    setUserWebHost(next.userWebHost);
    setGatewayWebHttpHost(next.gatewayWebHttpHost);
    setGatewayWebWsHost(next.gatewayWebWsHost);
    markClean(next);
  };

  useEffect(() => {
    if (!open) return;
    applyDefaults();
    // intentionally only when open flips true / remounts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('instanceForm.name'), invalid: !name.trim() },
      { label: t('instanceForm.gatewayConfigHost'), invalid: !gatewayConfigHost.trim() },
      { label: t('instanceForm.runtimeConfigHost'), invalid: !runtimeConfigHost.trim() },
      { label: t('instanceForm.userWebHost'), invalid: !userWebHost.trim() },
      { label: t('instanceForm.gatewayWebHttpHost'), invalid: !gatewayWebHttpHost.trim() },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceForm.fieldRequired', { field: missing.label }));
      return;
    }
    const requiredHosts: { label: string; value: string }[] = [
      { label: t('instanceForm.gatewayConfigHost'), value: gatewayConfigHost },
      { label: t('instanceForm.runtimeConfigHost'), value: runtimeConfigHost },
      { label: t('instanceForm.userWebHost'), value: userWebHost },
      { label: t('instanceForm.gatewayWebHttpHost'), value: gatewayWebHttpHost },
    ];
    for (const item of requiredHosts) {
      if (!isValidHttpUrl(item.value)) {
        toast('warn', t('instanceForm.hostInvalid', { field: item.label }));
        return;
      }
    }
    if (gatewayWebWsHost.trim() && !isValidHttpUrl(gatewayWebWsHost)) {
      toast('warn', t('instanceForm.hostInvalid', { field: t('instanceForm.gatewayWebWsHost') }));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('instanceForm.name'), value: name },
      { label: t('instanceForm.description'), value: description },
    ]);
    if (unsafeField) {
      toast('warn', t('instanceForm.unsafeText', { field: unsafeField }));
      return;
    }

    setSaving(true);
    try {
      await InstanceApi.create({
        jiuwenclaw_name: name.trim().slice(0, FIELD_MAX_LENGTH.jiuwenclaw_name),
        description: description.trim().slice(0, FIELD_MAX_LENGTH.description) || undefined,
        namespace: 'default',
        space_id: 'default',
        created_by: 'system',
        gateway_config_host: gatewayConfigHost.trim().slice(0, FIELD_MAX_LENGTH.gateway_config_host),
        runtime_config_host: runtimeConfigHost.trim().slice(0, FIELD_MAX_LENGTH.runtime_config_host),
        user_web_host: userWebHost.trim().slice(0, FIELD_MAX_LENGTH.user_web_host),
        gateway_web_http_host: gatewayWebHttpHost.trim().slice(
          0,
          FIELD_MAX_LENGTH.gateway_web_http_host,
        ),
        gateway_web_ws_host:
          gatewayWebWsHost.trim().slice(0, FIELD_MAX_LENGTH.gateway_web_ws_host) || undefined,
      });
      toast('success', t('success.created'));
      applyDefaults();
      onCreated();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t('topology.createInstance')}
      onClose={onClose}
      dirty={isDirty(draft)}
      size="md"
      footer={
        <>
          <ModalCancelButton />
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.submit')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div>
          <FieldLabel required>{t('instanceForm.name')}</FieldLabel>
          <LimitedTextInput
            value={name}
            maxLength={FIELD_MAX_LENGTH.jiuwenclaw_name}
            onChange={setName}
          />
        </div>
        <div>
          <FieldLabel>{t('instanceForm.description')}</FieldLabel>
          <LimitedTextInput
            value={description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={setDescription}
          />
        </div>
        <div>
          <FieldLabel required>{t('instanceForm.gatewayConfigHost')}</FieldLabel>
          <LimitedTextInput
            value={gatewayConfigHost}
            maxLength={FIELD_MAX_LENGTH.gateway_config_host}
            onChange={setGatewayConfigHost}
            placeholder={DEFAULT_GATEWAY_CONFIG_HOST}
          />
        </div>
        <div>
          <FieldLabel required>{t('instanceForm.runtimeConfigHost')}</FieldLabel>
          <LimitedTextInput
            value={runtimeConfigHost}
            maxLength={FIELD_MAX_LENGTH.runtime_config_host}
            onChange={setRuntimeConfigHost}
            placeholder={DEFAULT_RUNTIME_CONFIG_HOST}
          />
        </div>
        <div>
          <FieldLabel required>{t('instanceForm.userWebHost')}</FieldLabel>
          <LimitedTextInput
            value={userWebHost}
            maxLength={FIELD_MAX_LENGTH.user_web_host}
            onChange={setUserWebHost}
            placeholder={DEFAULT_USER_WEB_HOST}
          />
        </div>
        <div>
          <FieldLabel required>{t('instanceForm.gatewayWebHttpHost')}</FieldLabel>
          <LimitedTextInput
            value={gatewayWebHttpHost}
            maxLength={FIELD_MAX_LENGTH.gateway_web_http_host}
            onChange={setGatewayWebHttpHost}
            placeholder={DEFAULT_GATEWAY_WEB_HTTP_HOST}
          />
        </div>
        <div>
          <FieldLabel>{t('instanceForm.gatewayWebWsHost')}</FieldLabel>
          <LimitedTextInput
            value={gatewayWebWsHost}
            maxLength={FIELD_MAX_LENGTH.gateway_web_ws_host}
            onChange={setGatewayWebWsHost}
            placeholder={DEFAULT_GATEWAY_WEB_WS_HOST}
          />
        </div>
      </div>
    </Modal>
  );
}
