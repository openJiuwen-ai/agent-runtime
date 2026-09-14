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

type HostMode = 'auto' | 'manual';

const DEFAULT_NAMESPACE = 'default';

const HOST_SVC = {
  gateway: 'jiuwenclaw-gateway',
  runtime: 'jiuwenclaw-agent-runtime',
  web: 'jiuwenclaw-web',
} as const;

const HOST_PORT = {
  gatewayConfig: 8775,
  runtimeConfig: 8091,
  userWeb: 5173,
  gatewayWebHttp: 19002,
  gatewayWebWs: 19000,
} as const;

/** 手动模式默认短名（同命名空间，不含 ns 后缀） */
const DEFAULT_MANUAL_HOSTS = {
  gatewayHost: `http://${HOST_SVC.gateway}:${HOST_PORT.gatewayConfig}`,
  runtimeHost: `http://${HOST_SVC.runtime}:${HOST_PORT.runtimeConfig}`,
  userWebHost: `http://${HOST_SVC.web}:${HOST_PORT.userWeb}`,
  gatewayWebHttpHost: `http://${HOST_SVC.gateway}:${HOST_PORT.gatewayWebHttp}`,
  gatewayWebWsHost: `http://${HOST_SVC.gateway}:${HOST_PORT.gatewayWebWs}`,
} as const;

/** K8s DNS label：小写字母/数字/连字符，不以连字符首尾，最长 63 */
const K8S_NS_RE = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;

/** 与 instance_info 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  jiuwenclaw_name: 128,
  description: 4096,
  namespace: 63,
  gateway_host: 512,
  runtime_host: 512,
  user_web_host: 512,
  gateway_web_http_host: 512,
  gateway_web_ws_host: 512,
} as const;

type HostBundle = {
  gatewayHost: string;
  runtimeHost: string;
  userWebHost: string;
  gatewayWebHttpHost: string;
  gatewayWebWsHost: string;
};

function buildHostsFromNamespace(ns: string): HostBundle {
  const namespace = ns.trim();
  return {
    gatewayHost: `http://${HOST_SVC.gateway}.${namespace}:${HOST_PORT.gatewayConfig}`,
    runtimeHost: `http://${HOST_SVC.runtime}.${namespace}:${HOST_PORT.runtimeConfig}`,
    userWebHost: `http://${HOST_SVC.web}.${namespace}:${HOST_PORT.userWeb}`,
    gatewayWebHttpHost: `http://${HOST_SVC.gateway}.${namespace}:${HOST_PORT.gatewayWebHttp}`,
    gatewayWebWsHost: `http://${HOST_SVC.gateway}.${namespace}:${HOST_PORT.gatewayWebWs}`,
  };
}

function isValidK8sNamespace(ns: string): boolean {
  const value = ns.trim();
  return value.length > 0 && value.length <= 63 && K8S_NS_RE.test(value);
}

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

function HostPreview({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <div className="input mono text-xs opacity-80 truncate" title={value}>
        {value || '—'}
      </div>
    </div>
  );
}

export function CreateInstanceModal({ open, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const { markClean, isDirty } = useFormDirty(open);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [hostMode, setHostMode] = useState<HostMode>('manual');
  /** 仅自动页签使用；与手动页签互不影响 */
  const [autoNamespace, setAutoNamespace] = useState(DEFAULT_NAMESPACE);
  /** 仅手动页签使用；与自动页签互不影响 */
  const [manualHosts, setManualHosts] = useState<HostBundle>({ ...DEFAULT_MANUAL_HOSTS });
  const [saving, setSaving] = useState(false);

  const autoHosts = buildHostsFromNamespace(autoNamespace.trim() || DEFAULT_NAMESPACE);

  const draft = {
    name,
    description,
    hostMode,
    autoNamespace,
    manualHosts,
  };

  const applyDefaults = () => {
    const next = {
      name: '',
      description: '',
      hostMode: 'manual' as HostMode,
      autoNamespace: DEFAULT_NAMESPACE,
      manualHosts: { ...DEFAULT_MANUAL_HOSTS },
    };
    setName(next.name);
    setDescription(next.description);
    setHostMode(next.hostMode);
    setAutoNamespace(next.autoNamespace);
    setManualHosts(next.manualHosts);
    markClean(next);
  };

  useEffect(() => {
    if (!open) return;
    applyDefaults();
    // intentionally only when open flips true / remounts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const patchManual = (patch: Partial<HostBundle>) => {
    setManualHosts((prev) => ({ ...prev, ...patch }));
  };

  const submit = async () => {
    let hosts: HostBundle;
    let namespace: string | undefined;

    if (hostMode === 'auto') {
      const ns = autoNamespace.trim();
      if (!ns) {
        toast('warn', t('instanceForm.fieldRequired', { field: t('instanceForm.namespace') }));
        return;
      }
      if (!isValidK8sNamespace(ns)) {
        toast('warn', t('instanceForm.namespaceInvalid'));
        return;
      }
      namespace = ns.slice(0, FIELD_MAX_LENGTH.namespace);
      hosts = buildHostsFromNamespace(ns);
    } else {
      // 手动模式：请求体不带 namespace，由后端 schema/库默认处理
      hosts = manualHosts;
    }

    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('instanceForm.name'), invalid: !name.trim() },
      { label: t('instanceForm.gatewayHost'), invalid: !hosts.gatewayHost.trim() },
      { label: t('instanceForm.runtimeHost'), invalid: !hosts.runtimeHost.trim() },
      { label: t('instanceForm.userWebHost'), invalid: !hosts.userWebHost.trim() },
      { label: t('instanceForm.gatewayWebHttpHost'), invalid: !hosts.gatewayWebHttpHost.trim() },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('instanceForm.fieldRequired', { field: missing.label }));
      return;
    }
    const requiredHosts: { label: string; value: string }[] = [
      { label: t('instanceForm.gatewayHost'), value: hosts.gatewayHost },
      { label: t('instanceForm.runtimeHost'), value: hosts.runtimeHost },
      { label: t('instanceForm.userWebHost'), value: hosts.userWebHost },
      { label: t('instanceForm.gatewayWebHttpHost'), value: hosts.gatewayWebHttpHost },
    ];
    for (const item of requiredHosts) {
      if (!isValidHttpUrl(item.value)) {
        toast('warn', t('instanceForm.hostInvalid', { field: item.label }));
        return;
      }
    }
    if (hosts.gatewayWebWsHost.trim() && !isValidHttpUrl(hosts.gatewayWebWsHost)) {
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
        ...(namespace !== undefined ? { namespace } : {}),
        space_id: 'default',
        created_by: 'system',
        gateway_host: hosts.gatewayHost.trim().slice(0, FIELD_MAX_LENGTH.gateway_host),
        runtime_host: hosts.runtimeHost.trim().slice(0, FIELD_MAX_LENGTH.runtime_host),
        user_web_host: hosts.userWebHost.trim().slice(0, FIELD_MAX_LENGTH.user_web_host),
        gateway_web_http_host: hosts.gatewayWebHttpHost.trim().slice(
          0,
          FIELD_MAX_LENGTH.gateway_web_http_host,
        ),
        gateway_web_ws_host:
          hosts.gatewayWebWsHost.trim().slice(0, FIELD_MAX_LENGTH.gateway_web_ws_host) || undefined,
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

        <div className="flex flex-col items-center">
          <div className="tabs-bar" role="tablist" aria-label={t('instanceForm.hostMode')}>
            <button
              type="button"
              role="tab"
              aria-selected={hostMode === 'manual'}
              className={`tab ${hostMode === 'manual' ? 'active' : ''}`}
              onClick={() => setHostMode('manual')}
            >
              {t('instanceForm.hostModeManual')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={hostMode === 'auto'}
              className={`tab ${hostMode === 'auto' ? 'active' : ''}`}
              onClick={() => setHostMode('auto')}
            >
              {t('instanceForm.hostModeAuto')}
            </button>
          </div>
          <p className="text-xs text-muted mt-1 text-center">
            {hostMode === 'auto' ? t('instanceForm.hostModeAutoHint') : t('instanceForm.hostModeManualHint')}
          </p>
        </div>

        {hostMode === 'auto' ? (
          <>
            <div>
              <FieldLabel required>{t('instanceForm.namespace')}</FieldLabel>
              <LimitedTextInput
                value={autoNamespace}
                maxLength={FIELD_MAX_LENGTH.namespace}
                onChange={setAutoNamespace}
                placeholder={DEFAULT_NAMESPACE}
              />
            </div>
            <HostPreview label={t('instanceForm.gatewayHost')} value={autoHosts.gatewayHost} />
            <HostPreview label={t('instanceForm.runtimeHost')} value={autoHosts.runtimeHost} />
            <HostPreview label={t('instanceForm.userWebHost')} value={autoHosts.userWebHost} />
            <HostPreview
              label={t('instanceForm.gatewayWebHttpHost')}
              value={autoHosts.gatewayWebHttpHost}
            />
            <HostPreview label={t('instanceForm.gatewayWebWsHost')} value={autoHosts.gatewayWebWsHost} />
          </>
        ) : (
          <>
            <div>
              <FieldLabel required>{t('instanceForm.gatewayHost')}</FieldLabel>
              <LimitedTextInput
                value={manualHosts.gatewayHost}
                maxLength={FIELD_MAX_LENGTH.gateway_host}
                onChange={(gatewayHost) => patchManual({ gatewayHost })}
                placeholder={DEFAULT_MANUAL_HOSTS.gatewayHost}
              />
            </div>
            <div>
              <FieldLabel required>{t('instanceForm.runtimeHost')}</FieldLabel>
              <LimitedTextInput
                value={manualHosts.runtimeHost}
                maxLength={FIELD_MAX_LENGTH.runtime_host}
                onChange={(runtimeHost) => patchManual({ runtimeHost })}
                placeholder={DEFAULT_MANUAL_HOSTS.runtimeHost}
              />
            </div>
            <div>
              <FieldLabel required>{t('instanceForm.userWebHost')}</FieldLabel>
              <LimitedTextInput
                value={manualHosts.userWebHost}
                maxLength={FIELD_MAX_LENGTH.user_web_host}
                onChange={(userWebHost) => patchManual({ userWebHost })}
                placeholder={DEFAULT_MANUAL_HOSTS.userWebHost}
              />
            </div>
            <div>
              <FieldLabel required>{t('instanceForm.gatewayWebHttpHost')}</FieldLabel>
              <LimitedTextInput
                value={manualHosts.gatewayWebHttpHost}
                maxLength={FIELD_MAX_LENGTH.gateway_web_http_host}
                onChange={(gatewayWebHttpHost) => patchManual({ gatewayWebHttpHost })}
                placeholder={DEFAULT_MANUAL_HOSTS.gatewayWebHttpHost}
              />
            </div>
            <div>
              <FieldLabel>{t('instanceForm.gatewayWebWsHost')}</FieldLabel>
              <LimitedTextInput
                value={manualHosts.gatewayWebWsHost}
                maxLength={FIELD_MAX_LENGTH.gateway_web_ws_host}
                onChange={(gatewayWebWsHost) => patchManual({ gatewayWebWsHost })}
                placeholder={DEFAULT_MANUAL_HOSTS.gatewayWebWsHost}
              />
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
