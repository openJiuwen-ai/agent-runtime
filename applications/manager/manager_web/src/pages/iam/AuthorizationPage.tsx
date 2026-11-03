import { useRouter } from '../../router';
import { WorkspaceNote } from '../../components/WorkspaceNote';

type AuthorizationEntry = {
  title: string;
  description: string;
  href: string;
  action: string;
  detail: string;
};

/**
 * 授权是访问授权目录的总入口。
 *
 * 具体的用户、组织、角色仍由各自页面管理；这个页面只把“谁、通过什么角色、
 * 可以进入哪个集群”这条关系串起来，避免把“授权”误做成重复的角色列表。
 */
export function AuthorizationPage() {
  const { navigate } = useRouter();
  const entries: AuthorizationEntry[] = [
    {
      title: '用户',
      description: '维护可被授权的用户，并查看用户所属组织。',
      href: '/users',
      action: '管理用户',
      detail: '对象：用户身份',
    },
    {
      title: '组织',
      description: '用组织批量管理成员，再将组织授权到服务实例。',
      href: '/orgs',
      action: '管理组织',
      detail: '对象：组织与成员',
    },
    {
      title: '角色',
      description: '定义管理面和资源操作权限，控制谁可以进行配置。',
      href: '/roles',
      action: '管理角色',
      detail: '对象：角色与权限',
    },
    {
      title: '服务实例',
      description: '进入具体服务实例，为用户或组织配置集群准入和 Agent 使用范围。',
      href: '/instances',
      action: '配置服务实例',
      detail: '对象：集群准入与 Agent 授权',
    },
  ];

  return (
    <div className="authorization-workspace flex min-w-0 flex-col gap-5">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-0">
          <div className="page-title">授权</div>
          <div className="page-subtitle">
            把身份、组织、角色与服务实例连接起来，完成最终的访问授权。
          </div>
        </div>
      </div>

      <div className="authorization-columns">
        <section className="card authorization-panel">
          <div className="card-header">
            <div>
              <h2>授权准备</h2>
              <p className="text-sm text-muted">先准备可被授权的身份和角色。</p>
            </div>
            <span className="pill sm ok">3 项已就绪</span>
          </div>
          <div className="authorization-check-list">
            {entries.slice(0, 3).map((entry) => (
              <div className="authorization-check" key={entry.title}>
                <span className="statusDot ok" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <strong>{entry.title}</strong>
                  <p>{entry.description}</p>
                </div>
                <button className="btn sm" type="button" onClick={() => navigate(entry.href)}>{entry.action} →</button>
              </div>
            ))}
          </div>
        </section>
        <section className="card authorization-panel">
          <div className="card-header">
            <div>
              <h2>生效位置</h2>
              <p className="text-sm text-muted">准入和 Agent 使用范围在具体集群中生效。</p>
            </div>
            <button className="btn primary sm" type="button" onClick={() => navigate('/instances')}>查看集群</button>
          </div>
          <div className="authorization-check-list">
            <div className="authorization-check">
              <span className="statusDot ok" aria-hidden="true" />
              <div className="min-w-0 flex-1"><strong>测试集群</strong><p>已配置准入对象和 Agent 使用范围</p></div>
              <button className="btn sm" type="button" onClick={() => navigate('/instances/mock-cluster-demo/cluster-config')}>进入 →</button>
            </div>
            <div className="authorization-check">
              <span className="statusDot" aria-hidden="true" />
              <div className="min-w-0 flex-1"><strong>生产集群</strong><p>尚未配置准入对象</p></div>
              <button className="btn sm" type="button" onClick={() => navigate('/instances')}>开始 →</button>
            </div>
          </div>
        </section>
      </div>

      <section className="card authorization-relations">
        <div className="card-header">
          <div>
            <h2>授权关系示例</h2>
            <p className="text-sm text-muted">用一行看清“谁”在“哪个集群”可以做什么。</p>
          </div>
          <span className="pill sm muted">示例数据</span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>对象</th><th>服务实例</th><th>范围</th><th>状态</th><th>下一步</th></tr></thead>
            <tbody>
              <tr><td><strong>客服部组织</strong></td><td>测试集群</td><td>可进入 · 可用客服助手</td><td><span className="pill sm ok">已生效</span></td><td><button className="btn ghost sm" onClick={() => navigate('/instances/mock-cluster-demo/cluster-config')}>查看集群配置 →</button></td></tr>
              <tr><td><strong>生产运维角色</strong></td><td>生产集群</td><td>可配置集群</td><td><span className="pill sm warn">待准入</span></td><td><button className="btn ghost sm" onClick={() => navigate('/instances')}>配置服务实例 →</button></td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <WorkspaceNote title="先理解授权关系" action={<button className="btn primary sm" type="button" onClick={() => navigate('/instances')}>去配置服务实例 →</button>}>
        用户或组织是“谁”，角色是“能做什么”，服务实例是“在哪个集群生效”。先准备身份和角色，再到服务实例中配置集群准入及 Agent 使用范围。
      </WorkspaceNote>

    </div>
  );
}
