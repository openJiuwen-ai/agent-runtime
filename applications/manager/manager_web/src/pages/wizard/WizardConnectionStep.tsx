import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError, InstanceApi } from '../../services/api';
import type { InstanceDetail } from '../../types';
import { isValidHttpUrl } from '../../utils/url';
import { findUnsafeTextField } from '../../utils/safeText';
import { WizardStepHeading } from './WizardStepHeading';

const fields = ['jiuwenclaw_name', 'description', 'namespace', 'gateway_host', 'runtime_host', 'user_web_host', 'gateway_web_http_host', 'gateway_web_ws_host'] as const;
const values = (instance: InstanceDetail) => Object.fromEntries(fields.map(key => [key, instance[key] || ''])) as Record<typeof fields[number], string>;

export function WizardConnectionStep({ instance, onSaved, onBusyChange }: { instance: InstanceDetail; onSaved: () => void; onBusyChange: (value: boolean) => void }) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [baseline, setBaseline] = useState(() => values(instance));
  const [draft, setDraft] = useState(baseline);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const requestPending = useRef(false);
  const dirty = fields.some(key => baseline[key] !== draft[key]);
  useEffect(() => { onBusyChange(dirty || saving); }, [dirty, saving, onBusyChange]);
  const labels = [tr('集群名称', 'Cluster name'), tr('集群描述', 'Description'), tr('服务部署命名空间', 'Deployment namespace'), tr('Gateway 管理地址', 'Gateway management URL'), tr('Runtime 管理地址', 'Runtime management URL'), tr('用户面地址', 'User Web URL'), tr('Gateway 用户面 HTTP 地址', 'Gateway user HTTP URL'), tr('Gateway 用户面 WebSocket 基地址', 'Gateway user WebSocket base URL')];
  async function save(event: FormEvent) {
    event.preventDefault();
    if (requestPending.current) return;
    setError('');
    if (!draft.jiuwenclaw_name.trim() || !/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(draft.namespace) || draft.namespace.length > 63) { setError(tr('请填写集群名称和有效的部署命名空间。', 'Enter a cluster name and valid deployment namespace.')); return; }
    const unsafe = findUnsafeTextField([{ label: labels[0], value: draft.jiuwenclaw_name }, { label: labels[1], value: draft.description }]);
    if (unsafe) { setError(tr('请移除不安全的文本：', 'Remove unsafe text: ') + unsafe); return; }
    for (const key of fields.slice(3)) {
      if ((key !== 'gateway_web_ws_host' || draft[key]) && !isValidHttpUrl(draft[key])) { setError(tr('请填写有效的服务地址：', 'Enter a valid service URL: ') + labels[fields.indexOf(key)]); return; }
    }
    requestPending.current = true;
    setSaving(true);
    try {
      if (dirty) { const saved = await InstanceApi.update(instance.jiuwenclaw_id, draft); setBaseline(values(saved)); setDraft(values(saved)); }
      onSaved();
    } catch (cause) { setError(cause instanceof ApiError ? cause.detail : String(cause)); }
    finally { requestPending.current = false; setSaving(false); }
  }
  return <>
    <WizardStepHeading step={tr('第 1 步 · 接入服务', 'Step 1 · Connect services')} title={tr('连接已有服务', 'Connect existing services')} description={tr('服务实例已经登记，可在此核对或修改连接配置。命名空间必须与服务启动时实际使用的值一致。', 'The service instance is registered. Review or edit its connection settings. The namespace must match the actual deployment.')} />
    <form className="card wizard-card" onSubmit={event => void save(event)}>
    <fieldset disabled={saving} className="m-0 min-w-0 border-0 p-0"><div className="wizard-form-grid">{fields.map((key, index) => <label className={'label ' + (index > 2 ? 'wizard-form-wide' : '')} key={key}>{labels[index]}{![1, 7].includes(index) && ' *'}<input className={'input ' + (index > 1 ? 'mono' : '')} maxLength={index === 0 ? 128 : index === 1 ? 4096 : index === 2 ? 63 : 512} required={![1, 7].includes(index)} value={draft[key]} onChange={event => setDraft(value => ({ ...value, [key]: event.target.value }))} />{index === 2 && <span className="wizard-field-help">{tr('例如 ./deploy.sh up -n team-a 中的 team-a。修改命名空间不会改写你已经确认过的服务地址。', 'For example, team-a in ./deploy.sh up -n team-a. Changing this field keeps the endpoints you have confirmed.')}</span>}</label>)}</div></fieldset>
    {error && <p role="alert" className="text-danger mt-4">{error}</p>}
    <div className="wizard-actions"><span className="text-sm text-muted">{tr('更新现有实例，不会重复创建。', 'Updates this instance without creating another.')}</span><button className="btn primary" disabled={saving}>{saving ? tr('保存中…', 'Saving…') : tr('保存并配置集群准入 →', 'Save and configure admission →')}</button></div>
    </form>
  </>;
}
