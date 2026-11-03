import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { clusterPagePath, type ClusterWorkspaceSection } from '../../utils/clusterNavigation';
import { useClusterSetupProgress } from '../../hooks/useClusterSetupProgress';
import { ClusterWorkspaceNavigation } from '../../components/ClusterWorkspaceNavigation';
import { ClusterReadinessPanel } from './ClusterReadinessPanel';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import './ClusterConfigWorkbenchPage.css';

/** The stable home for a cluster. Re-entering always derives the next task from saved data. */
export function ClusterConfigWorkbenchPage({ instanceId }: { instanceId: string }) {
  const { i18n } = useTranslation();
  const { navigate, params } = useRouter();
  const state = useClusterSetupProgress(instanceId);
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  const open = (section: string) => {
    const [path, search = ''] = section.split('?');
    const [target, context = ''] = clusterPagePath(instanceId, path as ClusterWorkspaceSection, params).split('?');
    const query = new URLSearchParams(context);
    new URLSearchParams(search).forEach((value, key) => query.set(key, value));
    navigate(`${target}${query.size ? `?${query}` : ''}`);
  };
  const data = state.data;
  if (state.loading && !data) return <div className="card">{tr('正在读取集群配置…', 'Loading cluster configuration…')}</div>;
  if (state.error || !data) return <div className="card" role="alert"><p className="text-danger">{state.error || tr('无法读取集群配置', 'Unable to load cluster configuration')}</p><div className="mt-3 flex gap-2"><button className="btn" onClick={() => void state.reload()}>{tr('重试', 'Retry')}</button><button className="btn" onClick={() => navigate('/instances')}>{tr('返回集群管理', 'Return to clusters')}</button></div></div>;
  const steps = [
    { section: 'access', title: tr('集群准入', 'Cluster admission'), text: tr('选择可以进入当前集群的用户或组织。', 'Choose the users or organizations allowed to enter this cluster.'), summary: tr(`${data.admission.users} 个用户 · ${data.admission.orgs} 个组织`, `${data.admission.users} users · ${data.admission.orgs} organizations`), complete: data.admission.total > 0 },
    { section: 'service-resources', title: tr('运行资源', 'Runtime resources'), text: tr('选择运行规格，设置适用范围并启用。', 'Select runtime profiles, configure their scope and enable them.'), summary: tr(`${data.runtime.total} 个有效运行规格`, `${data.runtime.total} active runtime profiles`), complete: data.runtime.total > 0 },
    { section: 'agent-resources', title: tr('Agent 绑定', 'Agent bindings'), text: tr('绑定 Agent 模板，设置谁可以使用。', 'Bind Agent templates and choose who can use them.'), summary: tr(`${data.agents.total} 个有效 Agent 绑定`, `${data.agents.total} active Agent bindings`), complete: data.agents.total > 0 },
    { section: 'status', title: tr('检查并验证', 'Check and verify'), text: tr('检查组件连接，再到用户面验证实际对话。', 'Check component connectivity, then verify a conversation in the user console.'), summary: data.connected ? tr('组件已连接 · 对话需实际验证', 'Components connected · Conversation not verified') : tr('组件连接待检查', 'Connectivity needs checking'), complete: data.connected },
  ];
  const optionalSettings = [
    { id: 'log-masking', title: tr('日志脱敏', 'Log masking'), text: tr('隐藏日志中的手机号、密钥等敏感信息。', 'Hide phone numbers, secrets and other sensitive log data.') },
    { id: 'logging', title: tr('日志级别', 'Log levels'), text: tr('调整各组件日志详细程度，便于定位问题。', 'Adjust component verbosity for troubleshooting.') },
    { id: 'cron-quota', title: tr('定时任务配额', 'Scheduled task quotas'), text: tr('限制每位用户可启用的定时任务数量。', 'Limit active scheduled tasks per user.') },
    { id: 'team-quota', title: tr('团队配额', 'Team quotas'), text: tr('设置团队数量上限和团队成员规模。', 'Limit team counts and members per team.') },
  ];
  const next = steps.find(item => item.section === data.nextSection)!;
  return <div className="flex min-w-0 flex-col gap-4">
    <ClusterWorkspaceNavigation instanceId={instanceId} name={data.instance.jiuwenclaw_name} section="cluster-config" progress={data} onRefresh={() => void state.reload()} />
    <section className="card cluster-setup-overview">
      <div><span className="text-xs text-muted">{tr('配置进度', 'Setup progress')}</span><h2 className="mt-1 text-lg font-semibold">{data.configurationComplete ? tr('基础配置已保存，继续验证可用性', 'Configuration saved. Verify usability next.') : tr(`下一步：${next.title}`, `Next: ${next.title}`)}</h2><p className="mt-2 text-sm text-muted">{tr('保存过的配置会自动恢复。可以按顺序继续，也可以直接进入某一步修改。', 'Saved configuration is restored automatically. Continue in order or open any step to make changes.')}</p></div>
      <div className="cluster-setup-overview__progress"><strong>{data.completedConfigurations}<span> / 3</span></strong><span className="text-xs text-muted">{tr('基础配置已保存', 'Configuration steps saved')}</span><div className="wizard-progress mt-2 !mb-0"><span style={{ width: `${data.completedConfigurations / 3 * 100}%` }} /></div></div>
    </section>
    <section className="cluster-setup-grid" aria-label={tr('配置步骤', 'Setup steps')}>
      {steps.map((item, index) => <article className={`card cluster-setup-card ${item.section === data.nextSection ? 'next' : ''}`} key={item.section}>
        <div className="flex items-center justify-between gap-2"><span className={`cluster-setup-card__number ${item.complete ? 'complete' : ''}`}>{item.complete ? '✓' : index + 1}</span><span className={`pill sm ${item.complete ? 'ok' : ''}`}>{item.complete ? (index === 3 ? tr('已连接', 'Connected') : tr('已配置', 'Configured')) : (index === 3 ? tr('待检查', 'Needs checking') : tr('待配置', 'Needs setup'))}</span></div>
        <h2 className="mt-4 font-semibold">{item.title}</h2><p className="mt-2 text-sm text-muted">{item.text}</p><p className="mt-4 text-xs text-muted">{item.summary}</p><button className={`btn mt-4 ${item.section === data.nextSection ? 'primary' : ''}`} onClick={() => open(item.section)}>{item.complete ? tr('查看与修改', 'Review and edit') : tr('进入配置', 'Configure')} →</button>
      </article>)}
    </section>
    <div className="cluster-workbench-secondary">
      <section className="card cluster-workbench-options">
        <div className="card-header"><div><h2>{tr('可选配置', 'Optional settings')}</h2><p className="text-sm text-muted">{tr('按需补充日志与配额设置，每项独立保存。', 'Adjust logging and quotas as needed. Each setting saves independently.')}</p></div><button className="btn sm" onClick={() => open('other-settings')}>{tr('全部配置', 'All settings')} →</button></div>
        <div className="cluster-workbench-options__grid">{optionalSettings.map(item => <button className="cluster-workbench-option" key={item.id} onClick={() => open(`other-settings?setting=${item.id}`)}><span><strong>{item.title} · {tr('可选', 'Optional')}</strong><span className="block text-sm text-muted mt-2">{item.text}</span></span><span aria-hidden="true">→</span></button>)}</div>
      </section>
      <ClusterReadinessPanel key={instanceId} data={data} refreshing={state.loading} reload={state.reload} />
    </div>
    <WorkspaceNote title={tr('集群配置工作台', 'Cluster configuration workspace')}>{tr('本页汇总当前集群的准入、运行资源和 Agent 绑定。进度以已保存且有效的配置为准；实际可用还需要使用范围相互匹配、组件连接正常，并通过用户面对话验证。', 'This page summarizes admission, runtime resources and Agent bindings for this cluster. Progress reflects saved active settings. Actual use also requires matching scopes, healthy components and a verified conversation.')}</WorkspaceNote>
  </div>;
}
