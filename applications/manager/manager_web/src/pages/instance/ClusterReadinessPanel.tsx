import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StatusBadge } from '../../components/StatusBadge';
import { ApiError, InstanceApi } from '../../services/api';
import type { ClusterSetupProgress } from '../../utils/clusterSetupProgress';
import { useRouter } from '../../router';
import { clusterPagePath } from '../../utils/clusterNavigation';

export function ClusterReadinessPanel({ data, refreshing, reload }: {
  data: ClusterSetupProgress;
  refreshing: boolean;
  reload: () => Promise<void>;
}) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const id = data.instance.jiuwenclaw_id;
  const [probing, setProbing] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function checkConnections() {
    if (probing) return;
    setProbing(true); setError(''); setMessage('');
    try {
      const result = await InstanceApi.probeHealth();
      await reload();
      setMessage(tr(`连接检查已完成，共检查 ${result.probed} 个组件地址；下方显示当前集群结果。`, `Checked ${result.probed} component endpoints. Current cluster results are shown below.`));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally { setProbing(false); }
  }

  return <aside className="card cluster-readiness" aria-label={tr('配置与使用检查', 'Configuration and access checks')}>
    <div className="card-header"><h2>{tr('配置与使用检查', 'Configuration and access checks')}</h2><button className="btn sm" title={tr('检查全部集群的组件连接，此处展示当前集群结果。', 'Check all clusters and show results for this cluster.')} disabled={probing || refreshing} onClick={() => void checkConnections()}>{probing ? tr('检查中…', 'Checking…') : tr('检查连接', 'Check connections')}</button></div>
    <div className="cluster-readiness__section">
      <div className="cluster-readiness__connections" aria-label={tr('组件连接', 'Component connectivity')}>{([['Gateway', data.instance.gateway_status], ['Runtime', data.instance.runtime_status], ['User Web', data.instance.user_web_status || 'pending']] as const).map(([label, status]) => <div key={label} title={`${label}: ${status || 'pending'}`} aria-label={`${label}: ${status || 'pending'}`}><StatusBadge status={status || 'pending'} /><span className="text-xs text-muted">{label}</span></div>)}</div>
    </div>
    <div className="cluster-readiness__section">
      <strong className="text-sm">{tr('用户面验证', 'User console verification')}</strong>
      <p className="text-xs text-muted mt-2">{tr('请从独立的用户面入口登录测试账号，再选择此集群和 Agent 验证权限及对话。管理面登录状态不用于用户面对话。', 'Sign in with a test account at the separate user console, then select this cluster and an Agent to verify access and chat. Management sessions are separate from user sessions.')}</p>
    </div>
    <div className="cluster-readiness__actions">
      <button type="button" className="btn w-full" onClick={() => navigate(clusterPagePath(id, 'access', params))}>{tr('前往配置权限', 'Configure access')}</button>
      {message && <p className="text-xs text-muted" role="status">{message}</p>}
      {error && <p className="text-xs text-danger" role="alert">{error}</p>}
    </div>
  </aside>;
}
