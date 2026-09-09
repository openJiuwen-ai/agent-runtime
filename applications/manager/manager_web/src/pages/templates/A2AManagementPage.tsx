import { A2AAccessPoliciesPage } from './A2AAccessPoliciesPage';
import { A2AOutboundTemplatesPage } from './A2AOutboundTemplatesPage';
import { useRouter } from '../../router';

/** A2A 管理页：agents / policies 两个页签；页签状态同步到 ?tab= 以支持外链直达。 */
export function A2AManagementPage() {
  const { params, navigate } = useRouter();
  const tab = params.get('tab') === 'policies' ? 'policies' : 'agents';
  const setTab = (next: 'agents' | 'policies') => navigate(`/a2a-management${next === 'policies' ? '?tab=policies' : ''}`);

  const header = (
    <div className="min-w-[12rem] max-w-[32rem] shrink-0">
      <div className="page-title truncate" title="A2A 管理">A2A 管理</div>
      <div className="page-subtitle truncate" title="管理企业出站 A2A Agent 目录与 Agent 访问策略">管理企业出站 A2A Agent 目录与 Agent 访问策略</div>
    </div>
  );
  const tabs = (
    <div className="tabs-bar max-w-full overflow-x-auto" role="tablist" aria-label="A2A 管理">
      <button type="button" role="tab" aria-selected={tab === 'agents'} className={`tab ${tab === 'agents' ? 'active' : ''}`} onClick={() => setTab('agents')}>
        A2A Agent
      </button>
      <button type="button" role="tab" aria-selected={tab === 'policies'} className={`tab ${tab === 'policies' ? 'active' : ''}`} onClick={() => setTab('policies')}>
        访问策略
      </button>
    </div>
  );

  return tab === 'agents'
    ? <A2AOutboundTemplatesPage embedded header={header} tabs={tabs} />
    : <A2AAccessPoliciesPage header={header} tabs={tabs} />;
}
