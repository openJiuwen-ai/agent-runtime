import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/AuthContext';
import { StatusBadge } from '../../components/StatusBadge';
import { useAsync } from '../../hooks/useAsync';
import { ApiError, InstanceApi, UserConsoleApi } from '../../services/api';
import type { ClusterSetupProgress } from '../../utils/clusterSetupProgress';
import { clusterReadiness } from '../../utils/clusterReadiness';

export function ClusterReadinessPanel({ data, refreshing, reload }: {
  data: ClusterSetupProgress;
  refreshing: boolean;
  reload: () => Promise<void>;
}) {
  const { i18n } = useTranslation();
  const { user } = useAuth();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const id = data.instance.jiuwenclaw_id;
  const contexts = useAsync(async () => {
    const result = await UserConsoleApi.agentContexts();
    return { id, count: result.contexts.filter(row => row.jiuwenclaw_id === id).length };
  }, [id, user?.user_id, data]);
  const [probing, setProbing] = useState(false);
  const [opening, setOpening] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const count = contexts.data?.id === id ? contexts.data.count : 0;
  const checking = refreshing || probing || contexts.loading;
  const readiness = clusterReadiness({ configurationComplete: data.configurationComplete, connected: data.connected, identityLoading: checking, identityError: !!contexts.error, contextCount: count });
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

  async function openConsole() {
    if (readiness !== 'ready' || opening) return;
    setOpening(true); setError(''); setMessage('');
    try {
      // Recheck access at action time; saved counts alone do not authorize a user.
      const result = await UserConsoleApi.agentContexts();
      if (!result.contexts.some(row => row.jiuwenclaw_id === id)) {
        await contexts.reload();
        setError(tr('账号权限已变化，请核对集群准入和 Agent 使用范围。', 'Account access changed. Review admission and Agent scope.'));
        return;
      }
      await UserConsoleApi.setActiveCluster(id);
      window.location.assign('/chat/');
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail : String(cause));
    } finally { setOpening(false); }
  }

  return <aside className="card cluster-readiness" aria-label={tr('配置与使用检查', 'Configuration and access checks')}>
    <div className="card-header"><h2>{tr('配置与使用检查', 'Configuration and access checks')}</h2></div>
    <div className="cluster-readiness__section">
      <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{tr('组件连接', 'Component connectivity')}</strong><button className="btn sm" title={tr('检查全部集群的组件连接，此处展示当前集群结果。', 'Check all clusters and show results for this cluster.')} disabled={probing || refreshing} onClick={() => void checkConnections()}>{probing ? tr('检查中…', 'Checking…') : tr('检查连接', 'Check connections')}</button></div>
      <div className="space-y-3 text-sm mt-3">{([['Gateway', data.instance.gateway_status], ['Runtime', data.instance.runtime_status], ['User Web', data.instance.user_web_status || 'pending']] as const).map(([label, status]) => <div key={label} className="flex justify-between gap-2"><span>{label}</span><StatusBadge status={status} /></div>)}</div>
    </div>
    <div className="cluster-readiness__section">
      <strong className="text-sm">{tr('当前账号权限', 'Current account access')}</strong>
      <p className="text-xs text-muted mt-2">{contexts.loading ? tr('正在查询…', 'Checking…') : contexts.error ? tr('权限查询失败，请重试。', 'Access check failed. Retry to confirm.') : count ? tr(`已匹配 ${count} 个 Agent 使用上下文。`, `${count} Agent contexts available.`) : tr('暂无可用 Agent，请核对集群准入与 Agent 使用范围。', 'No Agent is available. Review cluster admission and Agent scope.')}</p>
      {contexts.error && <><p className="text-xs text-danger mt-2" role="alert">{contexts.error}</p><button className="btn sm mt-2" onClick={() => void contexts.reload()}>{tr('重新检查权限', 'Retry access check')}</button></>}
    </div>
    <div className="cluster-readiness__actions">
      <button className="btn primary w-full" disabled={readiness !== 'ready' || opening} onClick={() => void openConsole()}>{opening ? tr('正在进入…', 'Opening…') : tr('前往用户面验证', 'Test in user console')} →</button>
      {message && <p className="text-xs text-muted" role="status">{message}</p>}
      {error && <p className="text-xs text-danger" role="alert">{error}</p>}
    </div>
  </aside>;
}
