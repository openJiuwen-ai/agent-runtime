import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { DeploymentApi, ApiError } from '../../services/api';
import type { DeploymentDeployMode } from '../../types';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { toast } from '../../stores/uiStore';

/** 与 clusterCreationHosts 一致的服务/端口常量（host 推导预览）。 */
const HOST_SVC = {
  gateway: 'jiuwenswarm-gateway',
  runtime: 'jiuwenswarm-agent-runtime',
} as const;

const HOST_PORT = {
  gatewayConfig: 19000,
  runtimeConfig: 8091,
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
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

/** 发起部署向导页：与「注册集群」同一版式（左侧流程 + 主表单），成功后跳任务详情跟进执行进度。 */
export function CreateDeploymentPage() {
  const { t, i18n } = useTranslation();
  const { navigate } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;

  const [namespace, setNamespace] = useState('');
  const [name, setName] = useState('');
  const [action, setAction] = useState<'up' | 'down'>('up');
  const [deployMode, setDeployMode] = useState<DeploymentDeployMode>('full');
  const [waitTimeout, setWaitTimeout] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  /** 镜像 tag 批量值：应用到所有 *_IMAGE 键 */
  const [imageTag, setImageTag] = useState('');
  /** 逐键覆盖草稿（仅填写的键会提交；密码键留空不提交） */
  const [envDraft, setEnvDraft] = useState<Record<string, string>>({});
  const [envText, setEnvText] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const defaultsState = useAsync(() => DeploymentApi.getDefaults(), []);
  const defaults = defaultsState.data ?? null;

  const hostsPreview = buildHostsPreview(namespace);
  const envGroups = useMemo(() => groupEnvKeys(defaults?.env ?? {}), [defaults]);
  const deployDisabled = !!defaults && !defaults.deploy_enabled;

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

  const importEnvText = () => {
    const imported: Record<string, string> = {};
    for (const raw of envText.split('\n')) {
      const line = raw.trim();
      if (!line || line.startsWith('#') || !line.includes('=')) continue;
      const eq = line.indexOf('=');
      const key = line.slice(0, eq).trim();
      const value = line.slice(eq + 1).trim();
      if (!/^[A-Za-z0-9_]+$/.test(key)) continue;
      imported[key] =
        value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")
          ? value.slice(1, -1)
          : value;
    }
    const keys = Object.keys(imported);
    if (keys.length === 0) {
      toast('warn', t('deployments.form.envImportEmpty'));
      return;
    }
    setEnvDraft((prev) => ({ ...imported, ...prev }));
    toast('success', t('deployments.form.envImportDone', { count: keys.length }));
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
      <div
        key={key}
        className="flex items-center gap-2"
        title={sensitive ? key : `${key} = ${masked ?? ''}`}
      >
        <span className="mono text-[11px] text-muted shrink-0 basis-[14rem] truncate" title={key}>
          {key}
        </span>
        <div className="flex-1 min-w-0" title={sensitive ? undefined : masked}>
          <LimitedTextInput
            className="mono text-xs"
            value={envDraft[key] ?? ''}
            maxLength={512}
            placeholder={placeholder}
            type={sensitive ? 'password' : 'text'}
            onChange={(value) => setEnvDraft((prev) => ({ ...prev, [key]: value }))}
          />
        </div>
      </div>
    );
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (saving || deployDisabled) return;
    if (!namespace.trim()) {
      setError(t('instanceForm.fieldRequired', { field: t('instanceForm.namespace') }));
      return;
    }
    if (!isValidK8sNamespace(namespace.trim())) {
      setError(t('instanceForm.namespaceInvalid'));
      return;
    }
    if (!name.trim()) {
      setError(t('instanceForm.fieldRequired', { field: t('deployments.form.instanceName') }));
      return;
    }
    const timeoutRaw = waitTimeout.trim();
    let waitTimeoutSecs: number | undefined;
    if (timeoutRaw) {
      const parsed = Number(timeoutRaw);
      if (!Number.isInteger(parsed) || parsed < 60 || parsed > 14400) {
        setError(t('deployments.form.waitTimeoutInvalid'));
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
    setError('');
    try {
      const result = await DeploymentApi.create({
        action,
        namespace: namespace.trim().slice(0, NAMESPACE_MAX_LENGTH),
        jiuwenclaw_name: name.trim().slice(0, NAME_MAX_LENGTH),
        deploy_mode: deployMode,
        ...(action === 'up' && Object.keys(env).length > 0 ? { env } : {}),
        ...(action === 'up' && waitTimeoutSecs !== undefined
          ? { wait_timeout_secs: waitTimeoutSecs }
          : {}),
      });
      toast('success', t('deployments.createSuccess'));
      void navigate(`/deployments/${result.deployment_id}`);
    } catch (e) {
      setError(
        t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }),
      );
    } finally {
      setSaving(false);
    }
  };

  const flow = [
    [tr('发起部署', 'Start deployment'), tr('保存命名空间、实例名和部署参数', 'Save the namespace, instance name and parameters')],
    [tr('执行部署', 'Run deployment'), tr('校验、渲染、预检、应用自动依次执行', 'Validate, render, preflight and apply run in order')],
    [tr('注册服务实例', 'Register instance'), tr('成功后按命名空间自动登记服务实例', 'The instance is registered from the namespace on success')],
    [tr('继续集群配置', 'Continue setup'), tr('准入、运行资源、Agent 绑定与检查', 'Admission, runtime resources, Agent bindings and checks')],
  ];

  return <div className="wizard-workspace">
    <div className="page-header wizard-header">
      <div>
        <button type="button" className="btn ghost sm" disabled={saving} onClick={() => navigate('/deployments')}>← {t('deployments.title')}</button>
        <h1 className="page-title mt-3">{tr('发起部署', 'Start deployment')}</h1>
        <p className="page-subtitle">{tr('从管理面部署产品组件到目标命名空间，任务在后台执行，可随时回来查看进度。', 'Deploy product components into a namespace from Manager. The task runs in the background and progress is always available.')}</p>
      </div>
      <span className="tag">{tr('第 1 步 · 发起部署', 'Step 1 · Start deployment')}</span>
    </div>

    {defaults && !defaults.available && (
      <div className="card cost-hint" role="alert">
        <span>
          {t('deployments.unavailable')}
          {defaults.unavailable_reason ? `：${defaults.unavailable_reason}` : ''}
        </span>
      </div>
    )}
    {defaultsState.error && <div className="text-xs text-muted">{t('deployments.defaultsLoadFailed')}</div>}

    <div className="wizard-layout">
      <aside className="card wizard-steps">
        <h2 className="text-sm font-semibold">{tr('部署流程', 'Deployment flow')}</h2>
        <ol className="mt-3 flex flex-col gap-1" aria-label={tr('部署流程', 'Deployment flow')}>
          {flow.map(([title, description], index) => <li key={title} className={'wizard-step ' + (index === 0 ? 'current' : '')} aria-current={index === 0 ? 'step' : undefined}><span className="wizard-step__num">{index + 1}</span><span><strong>{title}</strong><small>{description}</small></span></li>)}
        </ol>
        {defaults?.tool_version && <p className="mt-4 text-xs text-muted">{tr('部署工具版本', 'Deploy tool version')} · <span className="mono">{defaults.tool_version}</span></p>}
      </aside>
      <main className="wizard-main">
        <form className="card wizard-card" onSubmit={event => void submit(event)}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="font-semibold">{tr('部署参数', 'Deployment parameters')}</h2>
              <p className="wizard-help">{tr('任务创建后在后台执行；同命名空间同时仅一个活跃任务。', 'The task runs in the background after creation; one active task per namespace.')}</p>
            </div>
            {deployDisabled && <span className="pill warn sm">{t('deployments.deployDisabled')}</span>}
          </div>
          <fieldset disabled={saving} className="m-0 min-w-0 border-0 p-0">
            <div>
              <FieldLabel required>{t('deployments.action.label')}</FieldLabel>
              <div className="flex gap-2" role="radiogroup">
                <button type="button" role="radio" aria-checked={action === 'up'} className={'btn sm ' + (action === 'up' ? 'primary' : 'ghost')} onClick={() => setAction('up')}>{t('deployments.action.up')}</button>
                <button type="button" role="radio" aria-checked={action === 'down'} className={'btn sm ' + (action === 'down' ? 'danger' : 'ghost')} onClick={() => setAction('down')}>{t('deployments.action.down')}</button>
              </div>
              <p className="text-xs text-muted mt-1">{action === 'up' ? t('deployments.action.upHint') : t('deployments.action.downHint')}</p>
            </div>

            <div className="wizard-form-grid">
              <label className="label" htmlFor="deploy-create-namespace">{t('instanceForm.namespace')} <span className="text-danger">*</span>
                <LimitedTextInput ariaLabel={t('instanceForm.namespace')} value={namespace} maxLength={NAMESPACE_MAX_LENGTH} placeholder="default" onChange={setNamespace} />
                <p className="text-xs text-muted mt-1">{t('deployments.form.namespaceHint')}</p>
              </label>
              <label className="label" htmlFor="deploy-create-name">{t('deployments.form.instanceName')} <span className="text-danger">*</span>
                <LimitedTextInput ariaLabel={t('deployments.form.instanceName')} value={name} maxLength={NAME_MAX_LENGTH} onChange={setName} />
                <p className="text-xs text-muted mt-1">{t('deployments.form.instanceNameHint')}</p>
              </label>
            </div>

            <div className={action === 'down' ? 'hidden' : ''}>
              <div className="wizard-form-grid">
                <label className="label" htmlFor="deploy-create-mode">{t('deployments.form.deployMode')} <span className="text-danger">*</span>
                  <select id="deploy-create-mode" className="select" value={deployMode} onChange={(e) => setDeployMode(e.target.value as DeploymentDeployMode)}>
                    <option value="full">{t('deployments.mode.full')}</option>
                    <option value="routing">{t('deployments.mode.routing')}</option>
                  </select>
                  <p className="text-xs text-muted mt-1">{deployMode === 'full' ? t('deployments.mode.fullHint') : t('deployments.mode.routingHint')}</p>
                </label>
                <label className="label" htmlFor="deploy-create-timeout">{t('deployments.form.waitTimeout')}
                  <input id="deploy-create-timeout" className="input" type="number" min={60} max={14400} value={waitTimeout} placeholder={t('deployments.form.waitTimeoutPlaceholder')} onChange={(e) => setWaitTimeout(e.target.value)} />
                  <p className="text-xs text-muted mt-1">{t('deployments.form.waitTimeoutHint')}</p>
                </label>
              </div>
            </div>

            {action === 'up' && <div className="wizard-section-heading"><strong>{tr('服务地址预览', 'Service endpoint preview')}</strong><span>{tr('部署成功后按命名空间自动注册实例。', 'The instance is registered from these endpoints on success.')}</span></div>}
            {action === 'up' && <div className="wizard-endpoint-preview">
              <div><span>{t('instanceForm.gatewayHost')}</span><code>{hostsPreview.gateway}</code></div>
              <div><span>{t('instanceForm.runtimeHost')}</span><code>{hostsPreview.runtime}</code></div>
            </div>}

            {action === 'up' && <div className="border-t border-border pt-2 mt-6">
              <button type="button" className="btn ghost sm" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen((v) => !v)}>
                <svg className={'w-3.5 h-3.5 transition-transform ' + (advancedOpen ? 'rotate-90' : '')} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                {t('deployments.form.advanced')}
              </button>
              {advancedOpen && (
                <div className="flex flex-col gap-3 mt-2">
                  <div>
                    <FieldLabel>{t('deployments.form.envImport')}</FieldLabel>
                    <textarea className="input mono text-xs min-h-[7rem]" value={envText} placeholder={t('deployments.form.envImportPlaceholder')} onChange={(e) => setEnvText(e.target.value)} spellCheck={false} />
                    <div className="flex items-center gap-2 mt-1">
                      <button type="button" className="btn sm" onClick={importEnvText}>{t('deployments.form.envImportBtn')}</button>
                    </div>
                  </div>
                  <div>
                    <FieldLabel>{t('deployments.form.imageTag')}</FieldLabel>
                    <input className="input mono text-xs" value={imageTag} placeholder={t('deployments.form.imageTagPlaceholder')} onChange={(e) => applyImageTagToAll(e.target.value)} />
                    <p className="text-xs text-muted mt-1">{t('deployments.form.imageTagHint')}</p>
                  </div>
                  {envGroups.images.length > 0 && (
                    <div>
                      <div className="label">{t('deployments.form.envGroupImages')}</div>
                      <div className="flex flex-col gap-2">{envGroups.images.map(renderEnvRow)}</div>
                    </div>
                  )}
                  {envGroups.replicas.length > 0 && (
                    <div>
                      <div className="label">{t('deployments.form.envGroupReplicas')}</div>
                      <div className="flex flex-col gap-2">{envGroups.replicas.map(renderEnvRow)}</div>
                    </div>
                  )}
                  {envGroups.others.length > 0 && (
                    <div>
                      <div className="label">{t('deployments.form.envGroupOthers')}</div>
                      <div className="flex flex-col gap-2">{envGroups.others.map(renderEnvRow)}</div>
                    </div>
                  )}
                  <p className="text-xs text-muted">{t('deployments.form.envHint')}</p>
                </div>
              )}
            </div>}
          </fieldset>

          {error && <p className="mt-4 text-sm text-danger break-words" role="alert">{error}</p>}
          <div className="wizard-actions">
            <button type="button" className="btn" disabled={saving} onClick={() => navigate('/deployments')}>{tr('暂时离开', 'Leave for now')}</button>
            <button type="submit" className="btn primary" disabled={saving || deployDisabled}>
              {saving ? t('common.loading') : action === 'up' ? tr('发起部署', 'Start deployment') : tr('发起卸载', 'Start undeploy')} →
            </button>
          </div>
        </form>
        <WorkspaceNote title={tr('部署任务由管理面后台执行', 'Deployment tasks run in the background')}>
          {tr('任务创建后按校验、渲染、预检、应用依次执行；部署成功会按命名空间自动注册服务实例，随后按集群准入 → 运行资源 → Agent 绑定 → 检查并验证继续配置。卸载会删除该命名空间的产品组件与 PVC 数据，实例记录与业务数据库需另行清理。',
              'Tasks run validate → render → preflight → apply in order. A successful deployment registers the instance automatically; continue with admission, runtime resources, Agent bindings and verification. Undeploy removes product components and PVC data in the namespace; instance records and databases are kept.')}
        </WorkspaceNote>
      </main>
    </div>
  </div>;
}
