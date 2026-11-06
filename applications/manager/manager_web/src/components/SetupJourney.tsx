import { useId, useState } from 'react';
import { create } from 'zustand';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';
import { useAuth } from '../auth/AuthContext';
import { InstanceApi, isPlatformAdmin } from '../services/api';
import { useAsync } from '../hooks/useAsync';
import { useGuideStore } from '../stores/guideStore';
import { collectPages } from '../utils/overview';
import { runtimeCatalogPath } from '../utils/runtimeResourceNavigation';
import { agentPreparationCatalogPath } from '../utils/agentFlowContext';
import { emptySetupGuide, setupGuideClusterPath, setupGuideRouteCluster, type SetupGuideSelection } from '../utils/setupGuide';
import './SetupJourney.css';

export const useSetupJourney = create<SetupGuideSelection & {
  open: (stage: number) => void;
  selectCluster: (clusterId: string) => void;
  toggleOptional: () => void;
}>(set => ({
  ...emptySetupGuide,
  open: stage => set({ stage }),
  selectCluster: clusterId => set({ clusterId }),
  toggleOptional: () => set(state => ({ showOptional: !state.showOptional })),
}));

type GuideTask = { title: string; description: string; href: string };

/** Stage and cluster selection only update the guide; action buttons navigate. */
export function SetupJourney({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const { stage, open, clusterId, selectCluster, showOptional, toggleOptional } = useSetupJourney();
  const { user } = useAuth();
  const { navigate, path, params } = useRouter();
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const revision = useGuideStore(state => state.revision);
  const selectId = useId();
  const admin = isPlatformAdmin(user);
  const clusters = useAsync(async () => admin ? collectPages((page, page_size) => InstanceApi.list({ page, page_size })) : [], [user?.user_id, admin, revision]);
  const routeCluster = setupGuideRouteCluster(path, params);
  const [clusterChoice, setClusterChoice] = useState<{ route: string; id: string } | null>(null);
  const requestedId = clusterChoice?.route === routeCluster ? clusterChoice.id : routeCluster || clusterId;
  const selected = clusters.data?.find(row => row.jiuwenclaw_id === requestedId);
  const targetId = !clusters.loading && !clusters.error && selected ? selected.jiuwenclaw_id : '';
  const to = (section: Parameters<typeof setupGuideClusterPath>[1]) => setupGuideClusterPath(targetId, section, path, params);
  const preparationContext = routeCluster === targetId ? params : new URLSearchParams();
  const go = (href: string | null) => { if (href) { onNavigate?.(); navigate(href); } };
  const current = Math.max(0, Math.min(stage, 3));
  const stages = [
    { title: tr('准备模型', 'Prepare models'), summary: tr('连接模型，按需添加技能与工具。', 'Connect a model and optionally add skills and tools.') },
    { title: tr('准备模板', 'Prepare templates'), summary: tr('定义 Agent 能力和运行方式。', 'Define Agent capabilities and runtime settings.') },
    { title: tr('配置集群', 'Configure a cluster'), summary: tr('完成准入、运行资源和 Agent 绑定。', 'Configure admission, runtime resources and Agent bindings.') },
    { title: tr('验证使用', 'Verify usage'), summary: tr('检查连接、账号权限与实际对话。', 'Check connectivity, account access and a real conversation.') },
  ];
  const tasks: GuideTask[][] = [
    [
      { title: tr('模型', 'Models'), description: tr('添加可调用的模型，供 Agent 模板选择。', 'Add callable models for Agent templates.'), href: '/model-templates' },
    ],
    [
      { title: tr('Agent模板管理', 'Agent templates'), description: tr('选择默认模型，按需组合技能和工具；保存为可复用的 Agent 模板。', 'Choose a default model and optional skills and tools, then save a reusable Agent template.'), href: agentPreparationCatalogPath(targetId, preparationContext) },
      { title: tr('运行时模板管理', 'Runtime templates'), description: tr('管理可复用的运行时模板，在集群中绑定使用。', 'Manage reusable runtime templates and bind them to a cluster.'), href: runtimeCatalogPath('service-config-templates', preparationContext, targetId) },
    ],
  ];
  const optional: GuideTask[] = [
    { title: tr('预置技能源', 'Prebuilt skills'), description: tr('为 Agent 提供可复用技能。', 'Provide reusable skills for Agents.'), href: '/skill-prebuilt-templates' },
    { title: tr('MCP服务', 'MCP services'), description: tr('连接外部工具服务。', 'Connect external tool services.'), href: '/mcp-templates' },
    { title: tr('工具安全策略', 'Tool safety policies'), description: tr('约束工具调用权限与行为。', 'Control tool permissions and behavior.'), href: '/safety-guardrails' },
    { title: tr('向量模型', 'Embedding models'), description: tr('为检索提供文本向量能力。', 'Provide embeddings for retrieval.'), href: '/embedding-templates' },
    { title: tr('A2A管理', 'A2A management'), description: tr('管理外部 Agent 接入与访问策略。', 'Manage external Agents and access policies.'), href: '/a2a-management' },
    { title: tr('专家与专家包', 'Experts and packages'), description: tr('组合专家能力，在 Agent 模板中引用。', 'Compose expert capabilities and reference them in Agent templates.'), href: '/expert-package-templates' },
    { title: tr('扩展配置', 'Extensions'), description: tr('添加业务需要的扩展参数。', 'Add extension settings for your workload.'), href: '/extension-config-templates' },
  ];
  const renderTask = (task: GuideTask) => <button type="button" key={task.title} className={`setup-guide-task ${task.href.split('?')[0] === path ? 'is-current' : ''}`} onClick={() => go(task.href)}>
    <strong>{task.title}<span aria-hidden="true">→</span></strong><span>{task.description}</span>
  </button>;

  if (!admin) return null;
  return <section className={`setup-guide ${compact ? 'setup-guide--compact' : 'card'}`} aria-label={tr('配置引导', 'Configuration guide')}>
    <div className="setup-guide-heading"><div><h2>{tr('配置引导', 'Configuration guide')}</h2><p>{tr('从资源准备到真实对话，按需复用已有配置。', 'From resource preparation to a real conversation, reuse existing configurations as needed.')}</p></div><span className="tag">{current + 1} / 4</span></div>
    <nav className="setup-guide-stages" aria-label={tr('配置阶段', 'Setup stages')}>{stages.map((item, index) => <button type="button" key={item.title} className={current === index ? 'is-current' : ''} aria-current={current === index ? 'step' : undefined} onClick={() => open(index)}><span className="setup-guide-number">{String(index + 1).padStart(2, '0')}</span><strong>{item.title}</strong><small>{item.summary}</small></button>)}</nav>
    {current >= 2 && <div className="setup-guide-context">
      <label htmlFor={selectId}>{tr('配置集群', 'Target cluster')}</label>
      <select id={selectId} className="select" value={selected?.jiuwenclaw_id || ''} disabled={clusters.loading || !!clusters.error} onChange={event => {
        const id = event.target.value;
        setClusterChoice({ route: routeCluster, id });
        selectCluster(id);
      }}>
        <option value="">{clusters.loading ? tr('正在加载集群…', 'Loading clusters…') : clusters.error ? tr('集群加载失败', 'Could not load clusters') : tr('选择已有集群', 'Choose an existing cluster')}</option>
        {(clusters.data || []).map(row => <option key={row.jiuwenclaw_id} value={row.jiuwenclaw_id}>{row.jiuwenclaw_name} · {row.jiuwenclaw_id.slice(0, 8)}</option>)}
      </select>
      <button type="button" className="btn primary sm" disabled={!targetId} title={!targetId ? tr('请先选择集群', 'Select a cluster first') : undefined} onClick={() => go(to('cluster-config'))}>{tr('前往配置工作台', 'Open configuration workspace')} →</button>
      <button type="button" className="btn sm" onClick={() => { setClusterChoice(null); selectCluster(''); open(2); go('/configuration-wizard?from=cluster'); }}>{tr('新建集群', 'New cluster')}</button>
    </div>}
    {current >= 2 && (clusters.error ? <p className="text-xs text-danger" role="alert">{clusters.error} <button type="button" className="btn ghost sm" onClick={() => void clusters.reload()}>{tr('重试', 'Retry')}</button></p> : requestedId && !clusters.loading && !selected && <p className="text-xs text-muted">{tr('之前的集群已不可用，请重新选择或新建。', 'The previous cluster is unavailable. Choose another or create one.')}</p>)}
    {current < 2 && <>
      <div className="setup-guide-task-heading"><h3>{stages[current].title}</h3><span>{tr('已有配置可直接复用', 'Reuse existing definitions')}</span></div>
      <div className={`setup-guide-tasks${current === 0 ? ' setup-guide-tasks--resources' : ''}`}>{tasks[current].map(renderTask)}</div>
    </>}
    {current === 2 && <div className="setup-guide-tasks">
      <button type="button" className="setup-guide-task" disabled={!targetId} onClick={() => go(to('access'))}><strong>{tr('集群准入', 'Cluster admission')}<span>→</span></strong><span>{tr('选择允许进入集群的用户与组织。', 'Choose the users and organizations allowed into the cluster.')}</span></button>
      <button type="button" className="setup-guide-task" disabled={!targetId} onClick={() => go(to('service-resources'))}><strong>{tr('绑定运行时模板', 'Bind a runtime template')}<span>→</span></strong><span>{tr('选择运行时模板，设置适用范围与优先级。', 'Choose a runtime template and set its scope and priority.')}</span></button>
      <button type="button" className="setup-guide-task" disabled={!targetId} onClick={() => go(to('agent-resources'))}><strong>{tr('绑定 Agent', 'Bind an Agent')}<span>→</span></strong><span>{tr('选择 Agent 模板，并设置哪些准入身份可以使用。', 'Select an Agent template and grant access to admitted identities.')}</span></button>
    </div>}
    {current === 3 && <div className="setup-guide-tasks"><button type="button" className="setup-guide-task" disabled={!targetId} onClick={() => go(to('status'))}><strong>{tr('检查连接与验证对话', 'Check connections and verify a conversation')}<span>→</span></strong><span>{tr('检查当前集群的组件连接，再进入用户面验证账号权限及 Agent 对话。保存模板不代表已完成实际运行验证。', 'Check the cluster connections, then verify account access and an Agent conversation in the user console. Saving templates does not verify execution.')}</span></button></div>}
    {current === 0 && <div className="setup-guide-optional"><button type="button" className="btn sm setup-guide-optional__toggle" aria-expanded={showOptional} onClick={toggleOptional}>{showOptional ? '−' : '+'} {tr('可选能力资源', 'Optional capability resources')}</button>{showOptional && <div className="setup-guide-tasks setup-guide-tasks--resources">{optional.map(renderTask)}</div>}</div>}
    <div className="setup-guide-footer"><span>{tr('保存后可随时返回继续；切换阶段仅浏览指引，不会标记配置完成。', 'Return after saving to continue. Switching stages does not mark configuration complete.')}</span><div>{current > 0 && <button type="button" className="btn sm" onClick={() => open(current - 1)}>{tr('上一阶段', 'Previous stage')}</button>}{current < 3 && <button type="button" className="btn sm" onClick={() => open(current + 1)}>{tr('下一阶段', 'Next stage')} →</button>}</div></div>
  </section>;
}
