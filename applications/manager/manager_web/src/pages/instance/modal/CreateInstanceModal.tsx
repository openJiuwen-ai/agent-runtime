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

/** 与 instance_info 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  jiuwenclaw_name: 128,
  description: 4096,
  gateway_config_host: 512,
  runtime_config_host: 512,
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
  const [saving, setSaving] = useState(false);

  const draft = { name, description, gatewayConfigHost, runtimeConfigHost };

  const applyDefaults = () => {
    const next = {
      name: '',
      description: '',
      gatewayConfigHost: DEFAULT_GATEWAY_CONFIG_HOST,
      runtimeConfigHost: DEFAULT_RUNTIME_CONFIG_HOST,
    };
    setName(next.name);
    setDescription(next.description);
    setGatewayConfigHost(next.gatewayConfigHost);
    setRuntimeConfigHost(next.runtimeConfigHost);
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
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceForm.fieldRequired', { field: missing.label }));
      return;
    }
    if (!isValidHttpUrl(gatewayConfigHost)) {
      toast('warn', t('instanceForm.hostInvalid', { field: t('instanceForm.gatewayConfigHost') }));
      return;
    }
    if (!isValidHttpUrl(runtimeConfigHost)) {
      toast('warn', t('instanceForm.hostInvalid', { field: t('instanceForm.runtimeConfigHost') }));
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
      </div>
    </Modal>
  );
}
