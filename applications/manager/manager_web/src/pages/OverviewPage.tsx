import { useSetupJourney } from '../components/SetupJourney';
import { collectPages, allComponentsConnected } from '../utils/overview';
import { useTranslation } from 'react-i18next';
import { InstanceApi, SystemApi } from '../services/api';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { StatusBadge } from '../components/StatusBadge';
import { WorkspaceNote } from '../components/WorkspaceNote';

export const loadOverviewInstances = () => collectPages((page, page_size) => InstanceApi.list({ page, page_size }));

export function OverviewPage() {
  const { i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  const { navigate } = useRouter();
  const instances = useAsync(loadOverviewInstances, []);
  const health = useAsync(() => SystemApi.health(), []);
  const rows = instances.data || [];
  const healthy = rows.filter(allComponentsConnected);
  const attention = rows.filter(r => !healthy.includes(r));
  const steps = [
    [tr('准备模型与工具', 'Prepare models & tools'), tr('连接模型，配置技能和外部工具。', 'Connect models, skills and external tools.'), '/model-templates'],
    [tr('创建服务实例', 'Create a service instance'), tr('登记实际运行 Agent 的集群和连接地址。', 'Register the cluster where Agents will run.'), '/configuration-wizard?from=cluster'],
    [tr('配置集群工作台', 'Configure the cluster workspace'), tr('完成准入、运行资源和 Agent 绑定。', 'Complete admission, runtime and Agent bindings.'), '/instances'],
    [tr('检查并完成', 'Review and finish'), tr('核对组件状态并进入用户面对话。', 'Verify components and open the user console.'), '/instances'],
  ];
  return <div className="workspace-overview">
    <div className="page-header"><div><div className="workspace-eyebrow">WORKSPACE</div><h1 className="page-title">{tr('管理总览', 'Management overview')}</h1><p className="page-subtitle">{tr('从服务连接到 Agent 使用，掌握配置与运行环境。', 'Manage service connectivity, Agent configuration and environments.')}</p></div>
      <button className="btn" disabled={instances.loading || health.loading} onClick={() => { void instances.reload(); void health.reload(); }}>{tr('刷新状态', 'Refresh')}</button></div>
    <div className="overview-metrics">
      <div className="card"><span className="text-muted">{tr('管理 API', 'Management API')}</span><strong>{health.loading ? '…' : health.error ? tr('连接失败', 'Unavailable') : health.data?.status === 'ok' ? tr('连接正常', 'Connected') : tr('状态未知', 'Unknown')}</strong><small>{health.error || tr('仅表示管理服务可访问', 'Management service connectivity only')}</small></div>
      <div className="card"><span className="text-muted">{tr('服务实例', 'Service instances')}</span><strong>{instances.data ? rows.length : '—'}</strong><small>{tr('已接入管理的服务环境', 'Registered service environments')}</small></div>
      <div className="card"><span className="text-muted">{tr('组件连接正常', 'Components connected')}</span><strong>{instances.data ? healthy.length : '—'}</strong><small>Gateway · Runtime · User Web</small></div>
      <div className="card"><span className="text-muted">{tr('待检查实例', 'Instances to inspect')}</span><strong>{instances.data ? attention.length : '—'}</strong><small>{tr('存在离线或未知组件', 'Offline or unknown components')}</small></div>
    </div>
    {instances.error && <div className="card text-danger" role="alert">{tr('无法读取实例：', 'Unable to load instances: ')}{instances.error}</div>}
    <section className="card overview-environments"><div className="card-header"><div><h2>{tr('服务环境', 'Service environments')}</h2><p className="text-muted text-sm">{tr('探活状态不代表 Agent 请求成功；进入实例进一步检查配置。', 'Connectivity does not guarantee successful Agent execution.')}</p></div><button className="btn" onClick={() => navigate('/instances')}>{tr('管理实例', 'Manage instances')} →</button></div>
      {instances.loading ? <p role="status" className="overview-empty">{tr('正在读取服务状态…', 'Loading service status…')}</p> : instances.data && !rows.length ? <div className="overview-empty"><h3>{tr('还没有接入服务实例', 'No service instances connected')}</h3><p>{tr('先准备能力资源，再接入环境并配置 Agent 使用范围。', 'Prepare resources, connect an environment and configure Agent access.')}</p><button className="btn primary" onClick={() => navigate('/configuration-wizard?from=cluster')}>{tr('新建集群', 'Create a cluster')} →</button></div> : instances.data && <div className="table-wrap"><table className="table"><thead><tr><th>{tr('名称', 'Name')}</th><th>Gateway</th><th>Runtime</th><th>User Web</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody>{[...attention, ...healthy].slice(0, 8).map(r => <tr key={r.jiuwenclaw_id}><td><b>{r.jiuwenclaw_name}</b><div className="text-muted text-xs">{r.namespace || '—'}</div></td><td><StatusBadge status={r.gateway_status} /></td><td><StatusBadge status={r.runtime_status} /></td><td><StatusBadge status={r.user_web_status} /></td><td><button className="btn ghost sm" onClick={() => navigate(`/instances/${r.jiuwenclaw_id}/status`)}>{tr('检查', 'Inspect')} →</button></td></tr>)}</tbody></table></div>}
    </section>
    <section><div className="card-header"><h2>{tr('让 Agent 可用的四个步骤', 'Four steps to make an Agent available')}</h2><span className="text-muted text-sm">{tr('配置路径', 'Configuration guide')}</span></div><div className="overview-steps">{steps.map(([name, desc, href], n) => <button className="card overview-step" key={name} onClick={() => { useSetupJourney.getState().open(n); navigate(href); }}><span className="step-number">0{n + 1}</span><h3>{name} <span aria-hidden="true">↗</span></h3><p>{desc}</p></button>)}</div></section>
    <WorkspaceNote title={tr('从资源准备到集群配置', 'From resource preparation to cluster setup')}>
      {tr('资源中心准备可复用定义，Agent模板管理负责组合能力，集群管理负责将准入、运行资源和 Agent 绑定到实际环境。', 'The resource catalog prepares reusable definitions, Agent management composes capabilities, and cluster management binds admission, runtime and Agents to a real environment.')}
    </WorkspaceNote>
  </div>;
}
