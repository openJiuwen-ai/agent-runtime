import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { DeploymentApi, ApiError } from '../../services/api';
import type { DeploymentDefaults, DeploymentDeployMode } from '../../types';
import { toast } from '../../stores/uiStore';

interface Props {
  open: boolean;
  defaults: DeploymentDefaults | null;
  onClose: () => void;
  /** 创建成功后回调（携带任务 ID，由列表页跳详情）。 */
  onCreated: (deploymentId: string) => void;
}

/** 与 CreateInstanceModal 保持一致的服务/端口常量（host 推导预览）。 */
const HOST_SVC = {
  gateway: 'jiuwenswarm-gateway',
  runtime: 'jiuwenswarm-agent-runtime',
  web: 'jiuwenswarm-web',
} as const;

const HOST_PORT = {
  gatewayConfig: 19000,
  runtimeConfig: 8091,
  userWeb: 5173,
} as const;

/** K8s DNS label：小写字母/数字/连字符，不以连字符首尾，最长 63 */
const K8S_NS_RE = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;

const NAME_MAX_LENGTH = 128;
const NAMESPACE_MAX_LENGTH = 63;

function isValidK8sNamespace(ns: string): boolean {
  const value = ns.trim();
  return value.length > 0 && value.length <= 63 && K8S_NS_RE.test(value);
}

function buildHostsPreview(ns: string) {
  const namespace = ns.trim() || 'default';
  return {
    gateway: `http://${HOST_SVC.gateway}.${namespace}:${HOST_PORT.gatewayConfig}`,
    runtime: `http://${HOST_SVC.runtime}.${namespace}:${HOST_PORT.runtimeConfig}`,
    userWeb: `http://${HOST_SVC.web}.${namespace}:${HOST_PORT.userWeb}`,
  };
}

/** 密码/密钥类键：值显示掩码占位，留空则不提交（避免误改）。 */
const SENSITIVE_KEY_MARKERS = ['PASSWORD', 'SECRET', 'ACCESS_KEY'];

function isSensitiveKey(key: string): boolean {
  const upper = key.toUpperCase();
  return SENSITIVE_KEY_MARKERS.some((marker) => upper.includes(marker));
}

/** env 键分组：镜像 / 副本 / 其他（保持 defaults 返回顺序）。 */
function groupEnvKeys(env: Record<string, string>) {
  const images: string[] = [];
  const replicas: string[] = [];
  const others: string[] = [];
  for (const key of Object.keys(env)) {
    if (key.endsWith('_IMAGE')) images.push(key);
    else if (key.endsWith('_REPLICAS')) replicas.push(key);
    else others.push(key);
  }
  return { images, replicas, others };
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

export function CreateDeploymentModal({ open, defaults, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const [namespace, setNamespace] = useState('');
  const [name, setName] = useState('');
  const [deployMode, setDeployMode] = useState<DeploymentDeployMode>('full');
  const [waitTimeout, setWaitTimeout] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  /** 镜像 tag 批量值：应用到所有 *_IMAGE 键 */
  const [imageTag, setImageTag] = useState('');
  /** 逐键覆盖草稿（仅填写的键会提交；密码键留空不提交） */
  const [envDraft, setEnvDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setNamespace('');
    setName('');
    setDeployMode('full');
    setWaitTimeout('');
    setAdvancedOpen(false);
    setImageTag('');
    setEnvDraft({});
    // 与 CreateInstanceModal 一致：仅在 open 翻转 true 时重置
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const hostsPreview = buildHostsPreview(namespace);
  const envGroups = useMemo(
    () => groupEnvKeys(defaults?.env ?? {}),
    [defaults],
  );

  const applyImageTagToAll = (tag: string) => {
    setImageTag(tag);
    const value = tag.trim();
    if (!value) return;
    setEnvDraft((prev) => {
      const next = { ...prev };
      for (const key of envGroups.images) {
        const base = (defaults?.env[key] ?? '').split(':').slice(0, -1).join(':');
        next[key] = base ? `${base}:${value}` : value;
      }
      return next;
    });
  };

  const submit = async () => {
    if (!namespace.trim()) {
      toast('warn', t('instanceForm.fieldRequired', { field: t('instanceForm.namespace') }));
      return;
    }
    if (!isValidK8sNamespace(namespace.trim())) {
      toast('warn', t('instanceForm.namespaceInvalid'));
      return;
    }
    if (!name.trim()) {
      toast('warn', t('instanceForm.fieldRequired', { field: t('instanceForm.name') }));
      return;
    }
    const timeoutRaw = waitTimeout.trim();
    let waitTimeoutSecs: number | undefined;
    if (timeoutRaw) {
      const parsed = Number(timeoutRaw);
      if (!Number.isInteger(parsed) || parsed < 60 || parsed > 14400) {
        toast('warn', t('deployments.form.waitTimeoutInvalid'));
        return;
      }
      waitTimeoutSecs = parsed;
    }

    const env: Record<string, string> = {};
    for (const [key, value] of Object.entries(envDraft)) {
      const trimmed = value.trim();
      // 密码/密钥键留空或保留掩码值时均不提交（服务端也会忽略 ******）
      if (!trimmed || trimmed === '******') continue;
      env[key] = trimmed;
    }

    setSaving(true);
    try {
      const result = await DeploymentApi.create({
        namespace: namespace.trim().slice(0, NAMESPACE_MAX_LENGTH),
        jiuwenclaw_name: name.trim().slice(0, NAME_MAX_LENGTH),
        deploy_mode: deployMode,
        ...(Object.keys(env).length > 0 ? { env } : {}),
        ...(waitTimeoutSecs !== undefined ? { wait_timeout_secs: waitTimeoutSecs } : {}),
      });
      toast('success', t('deployments.createSuccess'));
      onCreated(result.deployment_id);
    } catch (e) {
      toast(
        'danger',
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }),
      );
    } finally {
      setSaving(false);
    }
  };

  const renderEnvRow = (key: string) => {
    const sensitive = isSensitiveKey(key);
    const masked = defaults?.env[key];
    const isMasked = masked === '******';
    const placeholder = sensitive
      ? isMasked
        ? '******'
        : t('deployments.form.envSensitivePlaceholder')
      : masked ?? '';
    return (
      <div key={key} className="flex items-center gap-2">
        <span className="mono text-[11px] text-muted shrink-0 basis-[14rem] truncate" title={key}>
          {key}
        </span>
        <LimitedTextInput
          className="mono text-xs"
          value={envDraft[key] ?? ''}
          maxLength={512}
          placeholder={placeholder}
          type={sensitive ? 'password' : 'text'}
          onChange={(value) => setEnvDraft((prev) => ({ ...prev, [key]: value }))}
        />
      </div>
    );
  };

  return (
    <Modal
      open={open}
      title={t('deployments.createTitle')}
      onClose={onClose}
      size="lg"
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
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel required>{t('instanceForm.namespace')}</FieldLabel>
            <LimitedTextInput
              value={namespace}
              maxLength={NAMESPACE_MAX_LENGTH}
              placeholder="default"
              onChange={setNamespace}
            />
            <p className="text-xs text-muted mt-1">{t('deployments.form.namespaceHint')}</p>
          </div>
          <div>
            <FieldLabel required>{t('deployments.form.instanceName')}</FieldLabel>
            <LimitedTextInput
              value={name}
              maxLength={NAME_MAX_LENGTH}
              onChange={setName}
            />
            <p className="text-xs text-muted mt-1">{t('deployments.form.instanceNameHint')}</p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel required>{t('deployments.form.deployMode')}</FieldLabel>
            <select
              className="select"
              value={deployMode}
              onChange={(e) => setDeployMode(e.target.value as DeploymentDeployMode)}
            >
              <option value="full">{t('deployments.mode.full')}</option>
              <option value="routing">{t('deployments.mode.routing')}</option>
            </select>
            <p className="text-xs text-muted mt-1">
              {deployMode === 'full'
                ? t('deployments.mode.fullHint')
                : t('deployments.mode.routingHint')}
            </p>
          </div>
          <div>
            <FieldLabel>{t('deployments.form.waitTimeout')}</FieldLabel>
            <input
              className="input"
              type="number"
              min={60}
              max={14400}
              value={waitTimeout}
              placeholder={t('deployments.form.waitTimeoutPlaceholder')}
              onChange={(e) => setWaitTimeout(e.target.value)}
            />
            <p className="text-xs text-muted mt-1">{t('deployments.form.waitTimeoutHint')}</p>
          </div>
        </div>

        <HostPreview label={t('instanceForm.gatewayHost')} value={hostsPreview.gateway} />
        <HostPreview label={t('instanceForm.runtimeHost')} value={hostsPreview.runtime} />
        <HostPreview label={t('instanceForm.userWebHost')} value={hostsPreview.userWeb} />

        <div className="border-t border-border pt-2">
          <button
            type="button"
            className="btn ghost sm"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            <svg
              className={`w-3.5 h-3.5 transition-transform ${advancedOpen ? 'rotate-90' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
              aria-hidden
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            {t('deployments.form.advanced')}
          </button>
          {advancedOpen && (
            <div className="flex flex-col gap-3 mt-2">
              <div>
                <FieldLabel>{t('deployments.form.imageTag')}</FieldLabel>
                <input
                  className="input mono text-xs"
                  value={imageTag}
                  placeholder={t('deployments.form.imageTagPlaceholder')}
                  onChange={(e) => applyImageTagToAll(e.target.value)}
                />
                <p className="text-xs text-muted mt-1">{t('deployments.form.imageTagHint')}</p>
              </div>

              {envGroups.images.length > 0 && (
                <div>
                  <div className="label">{t('deployments.form.envGroupImages')}</div>
                  <div className="flex flex-col gap-2">
                    {envGroups.images.map(renderEnvRow)}
                  </div>
                </div>
              )}
              {envGroups.replicas.length > 0 && (
                <div>
                  <div className="label">{t('deployments.form.envGroupReplicas')}</div>
                  <div className="flex flex-col gap-2">
                    {envGroups.replicas.map(renderEnvRow)}
                  </div>
                </div>
              )}
              {envGroups.others.length > 0 && (
                <div>
                  <div className="label">{t('deployments.form.envGroupOthers')}</div>
                  <div className="flex flex-col gap-2">
                    {envGroups.others.map(renderEnvRow)}
                  </div>
                </div>
              )}
              <p className="text-xs text-muted">{t('deployments.form.envHint')}</p>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
