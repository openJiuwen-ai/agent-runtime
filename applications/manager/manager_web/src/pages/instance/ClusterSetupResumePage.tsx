import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useClusterSetupProgress } from '../../hooks/useClusterSetupProgress';
import { useRouter } from '../../router';
import { clusterPagePath } from '../../utils/clusterNavigation';

/** Resume from saved server configuration rather than wizard URLs or browser history. */
export function ClusterSetupResumePage({ instanceId }: { instanceId: string }) {
  const state = useClusterSetupProgress(instanceId);
  const { navigate, params } = useRouter();
  const { i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  useEffect(() => {
    if (!state.loading && state.data) navigate(clusterPagePath(instanceId, state.data.nextSection, params), { replace: true });
  }, [state.loading, state.data, instanceId, params, navigate]);
  if (state.error) return <div className="card" role="alert"><p className="text-danger">{state.error}</p><div className="flex gap-2 mt-3"><button className="btn" onClick={() => void state.reload()}>{zh ? '重新读取配置' : 'Retry'}</button><button className="btn" onClick={() => navigate(clusterPagePath(instanceId, 'cluster-config', params))}>{zh ? '返回配置工作台' : 'Configuration workspace'}</button></div></div>;
  return <div className="card" role="status">{zh ? '正在恢复已保存的配置，定位下一步…' : 'Restoring saved configuration and finding the next step…'}</div>;
}
