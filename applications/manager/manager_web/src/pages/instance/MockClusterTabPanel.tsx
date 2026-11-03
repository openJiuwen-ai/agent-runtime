import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { StatusBadge } from '../../components/StatusBadge';
import { clusterPagePath, type ClusterWorkspaceSection } from '../../utils/clusterNavigation';
import { admissionCatalogPath, admissionTab, clusterAccessPath } from '../../utils/admissionNavigation';
import { agentPreparationCatalogPath } from '../../utils/agentFlowContext';
import { runtimeCatalogPath } from '../../utils/runtimeResourceNavigation';

interface Props {
  tab: 'access' | 'status' | 'agentResources' | 'serviceResources' | 'expertPackages' | 'config' | 'quota' | 'workspaceQuota' | 'teamQuota' | 'tokenQuota' | 'cost' | 'audit';
}

const demoOrgs = [
  { name: '客服部', id: 'org-customer-service', policy: '允许登录', expires: '长期有效' },
];
const demoUsers = [
  { name: '演示用户 A', id: 'mock-user-a', policy: '允许登录', expires: '长期有效' },
  { name: '演示用户 B', id: 'mock-user-b', policy: '允许登录', expires: '长期有效' },
];
const demoClusterId = 'mock-cluster-demo';

function Intro({ title, text }: { title: string; text: string }) {
  return (
    <div className="page-header">
      <div className="min-w-0">
        <h2 className="page-title text-base">{title}</h2>
        <p className="page-subtitle">{text}</p>
      </div>
    </div>
  );
}

function DemoBadge() {
  return <span className="tag">本地演示数据</span>;
}

export function MockClusterTabPanel({ tab }: Props) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => (zh ? cn : en);
  const clusterPath = (section: ClusterWorkspaceSection) => clusterPagePath(demoClusterId, section, params);

  if (['config', 'quota', 'workspaceQuota', 'teamQuota', 'tokenQuota', 'cost', 'audit'].includes(tab)) {
    const labels: Record<string, [string, string]> = {
      config: ['基础配置', '管理日志、脱敏、审计和定时任务等运行策略。'],
      quota: ['配额管理', '控制当前集群的用户空间、团队和 Token 使用上限。'],
      workspaceQuota: ['用户空间配额', '按用户或组织限制工作空间容量。'],
      teamQuota: ['团队配额', '控制团队数量和成员规模。'],
      tokenQuota: ['Token 配额', '控制当前集群的 Token 使用量。'],
      cost: ['成本分析', '汇总当前集群的调用和运行资源用量。'],
      audit: ['审计记录', '追踪当前集群的准入、资源与运行策略变更。'],
    };
    const [title, description] = labels[tab];
    return <div className="flex flex-col gap-4">
      <Intro title={tr(title, title)} text={tr(description, description)} />
      <section className="card">
        <div className="card-header">
          <h3 className="card-title">{tr(title, title)}</h3>
          <DemoBadge />
        </div>
        <div className="instance-placeholder-state">
          <div className="instance-placeholder-state__icon" aria-hidden="true">◫</div>
          <div>
            <h3>{tr('暂无已接入的数据', 'No connected data')}</h3>
            <p>{tr('此演示页尚未接入该模块的数据接口，不能读取或保存相关配置。完成集群准入、运行资源或 Agent 绑定不会自动生成这里的数据。', 'This demo page is not connected to data services and cannot read or save these settings. Completing admission, runtime resources or Agent bindings will not populate this page.')}</p>
          </div>
        </div>
      </section>
      <div className="flex flex-wrap gap-2"><button className="btn" onClick={() => navigate(clusterPath('access'))}>{tr('配置集群准入', 'Configure admission')}</button><button className="btn" onClick={() => navigate(clusterPath('service-resources'))}>{tr('配置运行资源', 'Configure runtime')}</button><button className="btn" onClick={() => navigate(clusterPath('agent-resources'))}>{tr('绑定 Agent', 'Bind Agents')}</button><button className="btn" onClick={() => navigate(clusterPath('status'))}>{tr('检查连接状态', 'Check connectivity')}</button><button className="btn primary" onClick={() => navigate(clusterPath('cluster-config'))}>{tr('返回配置工作台', 'Cluster workspace')}</button></div>
    </div>;
  }

  if (tab === 'access') {
    const subjectTab = admissionTab(params);
    const isUsers = subjectTab === 'users';
    const rows = isUsers ? demoUsers : demoOrgs;
    return <div className="flex flex-col gap-4">
      <Intro title={tr('集群准入', 'Cluster admission')} text={tr('配置谁可以进入这个集群。保存准入后，继续配置运行资源、绑定 Agent，最后检查连接状态。', 'Choose who can enter this cluster. Then configure runtime resources, bind Agents and check connectivity.')} />
      <div className="tabs-bar"><button className={`tab ${!isUsers ? 'active' : ''}`} onClick={() => navigate(clusterAccessPath(demoClusterId, 'orgs', params))}>{tr('组织（1）', 'Organizations (1)')}</button><button className={`tab ${isUsers ? 'active' : ''}`} onClick={() => navigate(clusterAccessPath(demoClusterId, 'users', params))}>{tr('用户（2）', 'Users (2)')}</button></div>
      <section className="card !p-0 overflow-x-auto">
        <div className="card-header px-4"><div><h2>{isUsers ? tr('已准入的用户', 'Admitted users') : tr('已准入的组织', 'Admitted organizations')}</h2><p className="text-sm text-muted">{isUsers ? tr('用户准入规则决定该身份能否进入当前集群。', 'User grants determine whether an identity can enter this cluster.') : tr('组织成员默认继承该集群的进入权限。', 'Members inherit access to this cluster.')}</p></div><DemoBadge /></div>
        <table className="table min-w-[720px]"><thead><tr><th>{isUsers ? tr('用户', 'User') : tr('组织', 'Organization')}</th><th>ID</th><th>{tr('登录策略', 'Login policy')}</th><th>{tr('有效期', 'Expires')}</th><th>{tr('状态', 'Status')}</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><strong>{row.name}</strong></td><td className="mono text-xs text-muted">{row.id}</td><td>{row.policy}</td><td>{row.expires}</td><td><span className="tag">已启用</span></td><td><button className="btn ghost sm" onClick={() => navigate(admissionCatalogPath(subjectTab, params, demoClusterId))}>{isUsers ? tr('查看用户目录', 'View users') : tr('查看组织目录', 'View organizations')}</button></td></tr>)}</tbody></table>
      </section>
    </div>;
  }

  if (tab === 'status') {
    const components = [
      ['Gateway', 'online', '管理请求入口', 'http://demo-gateway.invalid'],
      ['Runtime', 'online', 'Agent 执行与运行池', 'http://demo-runtime.invalid'],
      ['User Web', 'online', '用户对话入口', 'http://demo-user-web.invalid'],
    ];
    return <div className="flex flex-col gap-4">
      <Intro title={tr('连接状态', 'Connectivity & status')} text={tr('按准入、运行资源、Agent 绑定的顺序完成配置后，检查三个组件连接，再验证实际对话。', 'After admission, runtime resources and Agent bindings are configured, check the three components and test a conversation.')} />
      <div className="page-header"><div><h2 className="text-base font-semibold">{tr('服务连接', 'Service connectivity')}</h2><p className="text-sm text-muted">{tr('以下状态为演示数据，不代表真实组件的连接结果。', 'These demo values do not represent connectivity checks against real components.')}</p></div><div className="flex gap-2"><DemoBadge /><button className="btn sm" onClick={() => navigate(clusterPath('cluster-config'))}>{tr('回到配置工作台', 'Cluster workspace')}</button></div></div>
      <div className="grid gap-4 md:grid-cols-3">{components.map(([name, status, desc, host]) => <section className="card" key={name}><div className="card-header"><div><h3>{name}</h3><p className="text-sm text-muted">{desc}</p></div><StatusBadge status={status} /></div><div className="mono text-xs break-all text-muted">{host}</div><div className="mt-4 text-xs text-muted">最近探活：刚刚</div></section>)}</div>
      <section className="card"><h2 className="card-title">{tr('配置概况', 'Configuration summary')}</h2><p className="mt-2 text-sm">{tr('已准入 1 个组织、2 个用户；已启用 2 个运行规格，已绑定 1 个 Agent。', '1 organization and 2 users admitted; 2 runtime profiles enabled; 1 Agent bound.')}</p><p className="mt-2 text-sm text-muted">{tr('准入、运行资源和 Agent 使用范围需同时有效且相互匹配。组件可达后仍需验证实际对话。', 'Admission, runtime resources and Agent scopes must be active and match. A reachable component still requires a successful conversation test.')}</p></section>
    </div>;
  }

  if (tab === 'agentResources') {
    return <div className="flex flex-col gap-4">
      <Intro title={tr('Agent绑定', 'Agent bindings')} text={tr('决定已准入的用户或组织可以使用哪个 Agent。这里不修改全局 Agent 定义。', 'Choose which admitted identities can use each Agent. Global definitions remain unchanged.')} />
      <div className="page-header"><div><h2 className="text-base font-semibold">{tr('当前集群已绑定 Agent', 'Agents bound to this cluster')}</h2><p className="text-sm text-muted">{tr('授权条件为空时，表示当前集群内所有已准入身份。', 'An empty condition means all admitted identities in this cluster.')}</p></div><div className="flex gap-2"><DemoBadge /><button className="btn primary sm" onClick={() => navigate(agentPreparationCatalogPath(demoClusterId, params))}>{tr('查看 Agent 模板目录', 'View template catalog')}</button></div></div>
      <section className="card !p-0 overflow-x-auto"><table className="table min-w-[840px]"><thead><tr><th>{tr('Agent', 'Agent')}</th><th>{tr('授权范围', 'Access scope')}</th><th>{tr('运行资源', 'Runtime')}</th><th>{tr('状态', 'Status')}</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody><tr><td><strong>客服助手</strong><div className="mono text-xs text-muted">mock-agent-customer-service</div><div className="text-xs text-muted mt-1">回答客服知识库问题</div></td><td>{tr('实例内已准入的全体用户', 'All admitted users in this cluster')}</td><td><span className="tag">默认运行池</span><div className="text-xs text-muted mt-1">优先级 10</div></td><td><span className="tag">已启用</span></td><td><button className="btn ghost sm" onClick={() => navigate(clusterPath('service-resources'))}>{tr('查看运行资源', 'View runtime')}</button></td></tr></tbody></table></section>
      <section className="card"><div className="card-header"><div><h2>{tr('三者关系', 'How the pieces connect')}</h2><p className="text-sm text-muted">{tr('准入、运行资源、Agent 使用范围需要同时满足，用户面才会展示可用 Agent。', 'Admission, runtime resources and Agent scope must all match before the user console can show an Agent.')}</p></div></div><div className="grid gap-3 md:grid-cols-3"><div className="surface-card"><strong>{tr('能进入', 'Can enter')}</strong><p className="text-sm text-muted">instance_grant</p></div><div className="surface-card"><strong>{tr('能运行', 'Can run')}</strong><p className="text-sm text-muted">instance_service_resource</p></div><div className="surface-card"><strong>{tr('能使用', 'Can use')}</strong><p className="text-sm text-muted">instance_agent_resource</p></div></div></section>
    </div>;
  }

  if (tab === 'serviceResources') {
    return <div className="flex flex-col gap-4">
      <Intro title={tr('运行资源', 'Runtime resources')} text={tr('为当前集群启用可调度的运行规格，Agent 会在会话触发时按优先级选择运行池。', 'Enable schedulable runtime profiles for this cluster. Agents choose a pool by priority when a session starts.')} />
      <div className="page-header"><div><h2 className="text-base font-semibold">{tr('当前集群的运行资源', 'Runtime resources for this cluster')}</h2><p className="text-sm text-muted">{tr('运行规格目录是可复用定义，本页决定它是否在当前集群生效。', 'The catalog stores reusable profiles; this page activates them for the current cluster.')}</p></div><div className="flex gap-2"><DemoBadge /><button className="btn primary sm" onClick={() => navigate(runtimeCatalogPath('service-config-templates', params, demoClusterId))}>{tr('查看运行规格目录', 'View runtime catalog')}</button></div></div>
      <section className="card !p-0 overflow-x-auto"><table className="table min-w-[760px]"><thead><tr><th>{tr('运行规格', 'Profile')}</th><th>{tr('授权范围', 'Scope')}</th><th>{tr('优先级', 'Priority')}</th><th>{tr('状态', 'Status')}</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody><tr><td><strong>客服运行池</strong><div className="mono text-xs text-muted">mock-runtime-customer-service</div></td><td>{tr('实例内已准入的全体用户', 'All admitted identities')}</td><td className="mono">0</td><td><span className="tag">已启用</span></td><td><button className="btn ghost sm" onClick={() => navigate(clusterPath('cluster-config'))}>{tr('返回工作台', 'Cluster workspace')}</button></td></tr><tr><td><strong>轻量运行池</strong><div className="mono text-xs text-muted">mock-runtime-lite</div></td><td>{tr('客服类 Agent', 'Customer service Agents')}</td><td className="mono">10</td><td><span className="tag">已启用</span></td><td><button className="btn ghost sm" onClick={() => navigate(clusterPath('agent-resources'))}>{tr('查看 Agent', 'View Agents')}</button></td></tr></tbody></table></section>
    </div>;
  }

  return <div className="flex flex-col gap-4">
    <Intro title={tr('专家包', 'Expert packages')} text={tr('按 Agent 模板或 Agent 分组控制当前集群可安装的专家包。', 'Control which expert packages are available in this cluster by Agent template or group.')} />
    <div className="page-header"><div><h2 className="text-base font-semibold">{tr('当前集群的专家包白名单', 'Expert package allowlist')}</h2><p className="text-sm text-muted">{tr('这是可选扩展，不会替代准入或 Agent 使用授权。', 'This optional extension does not replace admission or Agent access.')}</p></div><DemoBadge /></div>
    <section className="card !p-0 overflow-x-auto"><table className="table min-w-[760px]"><thead><tr><th>{tr('专家包', 'Package')}</th><th>{tr('授权对象', 'Granted to')}</th><th>{tr('版本', 'Version')}</th><th>{tr('状态', 'Status')}</th><th>{tr('操作', 'Actions')}</th></tr></thead><tbody><tr><td><strong>客服知识库增强包</strong><div className="mono text-xs text-muted">pkg-customer-knowledge</div></td><td><span className="tag">Agent 模板</span><div className="text-sm mt-1">客服助手</div></td><td className="mono">1.2.0</td><td><span className="tag">已允许</span></td><td><button className="btn ghost sm" onClick={() => navigate(clusterPath('agent-resources'))}>{tr('查看关联 Agent', 'View related Agent')}</button></td></tr><tr><td><strong>FAQ 检索工具包</strong><div className="mono text-xs text-muted">pkg-faq-search</div></td><td><span className="tag">Agent 分组</span><div className="text-sm mt-1">客服类 Agent</div></td><td className="mono">0.9.3</td><td><span className="tag">已允许</span></td><td><button className="btn ghost sm" onClick={() => navigate(clusterPath('cluster-config'))}>{tr('返回工作台', 'Cluster workspace')}</button></td></tr></tbody></table></section>
  </div>;
}
