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
    { title: tr('准备资源', 'Prepare resources'), summary: tr('连接模型，准备容器与可选能力。', 'Connect models, containers and optional capabilities.') },
    { title: tr('定义模板', 'Define templates'), summary: tr('定义 Agent 能力和运行方式。', 'Define Agent capabilities and runtime profiles.') },
    { title: tr('配置集群', 'Configure a cluster'), summary: tr('完成准入、运行资源和 Agent 绑定。', 'Configure admission, runtime resources and Agent bindings.') },
    { title: tr('验证使用', 'Verify usage'), summary: tr('检查连接、账号权限与实际对话。', 'Check connectivity, account access and a real conversation.') },
  ];
  const tasks: GuideTask[][] = [
    [
      { title: tr('模型', 'Models'), description: tr('添加可调用的模型，下一步在 Agent 模板中选择默认模型。', 'Add a model, then select it as the default in an Agent template.'), href: '/model-templates' },
      { title: tr('容器模板', 'Container templates'), description: tr('准备 AgentServer 等镜像和容器参数，供运行时模板引用。', 'Prepare AgentServer images and container settings for runtime templates.'), href: runtimeCatalogPath('container-templates', preparationContext, targetId) },
    ],
    [
      { title: tr('Agent模板管理', 'Agent templates'), description: tr('选择默认模型，按需组合技能和工具；保存为可复用的 Agent 模板。', 'Choose a default model and optional skills and tools, then save a reusable Agent template.'), href: agentPreparationCatalogPath(targetId, preparationContext) },
      { title: tr('运行时模板管理', 'Runtime templates'), description: tr('引用容器模板，设置运行参数和调度方式；已有模板可直接复用。', 'Reference container templates and configure runtime and scheduling settings, or reuse an existing template.'), href: runtimeCatalogPath('service-config-templates', preparationContext, targetId) },
    ],
  ];
  const optional: GuideTask[] = [
    { title: tr('预置技能源', 'Prebuilt skills'), description: tr('为 Agent 提供可复用技能。', 'Provide reusable skills for Agents.'), href: '/skill-prebuilt-templates' },
    { title: tr('MCP服务', 'MCP services'), description: tr('连接外部工具服务。', 'Connect external tool services.'), href: '/mcp-templates' },
    { title: tr('工具安全策略', 'Tool safety policies'), description: tr('约束工具调用权限与行为。', 'Control tool permissions and behavior.'), href: '/safety-guardrails' },
    { title: tr('向量模型', 'Embedding models'), description: tr('为检索提供文本向量能力。', 'Provide embeddings for retrieval.'), href: '/embedding-templates' },
    { title: tr('A2A管理', 'A2A management'), description: tr('管理外部 Agent 接入与访问策略。', 'Manage external Agents and access policies.'), href: '/a2a-management' },
    { title: tr('专家与专家包', 'Experts and packages'), description: tr('管理专家能力；模板绑定目前为预览。', 'Manage expert capabilities; template binding is currently a preview.'), href: '/expert-packages' },
    { title: tr('扩展配置', 'Extensions'), description: tr('添加业务需要的扩展参数。', 'Add extension settings for your workload.'), href: '/extension-config-templates' },
  ];
  const renderTask = (task: GuideTask) => <button type="button" key={task.title} className={`setup-guide-task ${task.href.split('?')[0] === path ? 'is-current' : ''}`} onClick={() => go(task.href)}>
    <strong>{task.title}<span aria-hidden="true">→</span></strong><span>{task.description}</span>
  </button>;

  if (!admin) return null;
  return <section className={`setup-guide ${compact ? 'setup-guide--compact' : 'card'}`} aria-label={tr('配置引导', 'Configuration guide')}>
    <div className="setup-guide-heading"><div><h2>{tr('配置引导', 'Configuration guide')}</h2><p>{tr('从资源准备到真实对话，按需复用已有配置。', 'From preparing resources to a real conversation. Reuse existing configuration where available.')}</p></div><span className="tag">{current + 1} / 4</span></div>
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
      <div className="setup-guide-tasks">{tasks[current].map(renderTask)}</div>
    </>}
    {current === 0 && <div className="setup-guide-optional"><button type="button" className="btn ghost sm" aria-expanded={showOptional} onClick={toggleOptional}>{showOptional ? '−' : '+'} {tr('可选能力资源', 'Optional capability resources')}</button>{showOptional && <div className="setup-guide-tasks">{optional.map(renderTask)}</div>}</div>}
    <div className="setup-guide-footer"><span>{tr('保存后可随时返回继续；切换阶段仅浏览指引，不会标记配置完成。', 'Return after saving to continue. Switching stages does not mark configuration complete.')}</span><div>{current > 0 && <button type="button" className="btn sm" onClick={() => open(current - 1)}>{tr('上一阶段', 'Previous stage')}</button>}{current < 3 && <button type="button" className="btn sm" onClick={() => open(current + 1)}>{tr('查看下一阶段', 'View next stage')} →</button>}</div></div>
  </section>;
}
