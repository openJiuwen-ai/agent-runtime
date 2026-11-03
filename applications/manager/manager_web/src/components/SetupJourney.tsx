import { create } from 'zustand';
import { useTranslation } from 'react-i18next';
import { matchRoute, useRouter } from '../router';
import { useAuth } from '../auth/AuthContext';
import { isPlatformAdmin } from '../services/api';
import { clusterPagePath } from '../utils/clusterNavigation';

export const useSetupJourney = create<{ stage: number; showOptional: boolean; open: (stage: number) => void; toggleOptional: () => void; }>(set => ({ stage: 0, showOptional: false, open: stage => set({ stage }), toggleOptional: () => set(state => ({ showOptional: !state.showOptional })) }));

/** The global menu points to the same cluster pages as the workspace navigation. */
export function SetupJourney({ compact = false }: { compact?: boolean }) {
  const { stage, open } = useSetupJourney();
  const { user } = useAuth();
  const { navigate, path, params } = useRouter();
  const { i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const tr = (cn: string, en: string) => zh ? cn : en;
  if (!isPlatformAdmin(user)) return null;
  const cluster = matchRoute('/instances/:id/:section', path) || matchRoute('/instances/:id', path) || ((params.get('cluster') || params.get('admissionCluster')) ? { id: (params.get('cluster') || params.get('admissionCluster'))! } : null);
  const to = (section: 'cluster-config' | 'access' | 'service-resources' | 'agent-resources' | 'status') => cluster ? clusterPagePath(cluster.id, section, params) : '/instances';
  const stages = [tr('服务实例', 'Service instance'), tr('集群准入', 'Admission'), tr('运行资源与 Agent', 'Runtime and Agents'), tr('检查并验证', 'Check and verify')];
  const tasks: Array<Array<[string, string, string]>> = [
    [[cluster ? tr('查看当前集群', 'Review this cluster') : tr('新建或选择集群', 'Create or select a cluster'), tr('新集群先登记连接地址；已有集群从列表继续配置。', 'Register endpoints for a new cluster, or resume an existing cluster from the list.'), cluster ? to('cluster-config') : '/instances']],
    [[tr('配置集群准入', 'Configure admission'), tr('允许哪些用户或组织进入集群，两者至少配置一种。', 'Allow users or organizations to enter the cluster; configure at least one.'), to('access')]],
    [[tr('先配置运行资源', 'Configure runtime resources first'), tr('启用运行规格，并设置适用范围和优先级。', 'Enable runtime profiles and set their scope and priority.'), to('service-resources')],
     [tr('再绑定 Agent', 'Then bind Agents'), tr('选择 Agent 模板，设置谁可以使用并保存绑定。', 'Choose Agent templates, set their access scope and save the bindings.'), to('agent-resources')]],
    [[tr('检查连接与实际使用', 'Check connectivity and actual use'), tr('检查组件连接，并使用已准入身份在用户面验证对话。', 'Check connectivity, then validate a conversation with an admitted identity.'), to('status')]],
  ];
  const current = Math.max(0, Math.min(stage, stages.length - 1));
  return <section className={'setup-journey ' + (compact ? 'setup-journey--popover' : '')} aria-label={tr('集群配置引导', 'Cluster setup guide')}>
    <div className="setup-journey-heading"><strong>{tr('集群配置引导', 'Cluster setup guide')} · {current + 1}/4 · {stages[current]}</strong></div>
    <nav className="setup-stage-nav" aria-label={tr('配置阶段', 'Setup stages')}>{stages.map((name, index) => <button key={name} className={current === index ? 'active' : ''} aria-current={current === index ? 'step' : undefined} onClick={() => open(index)}>{index + 1}. {name}</button>)}</nav>
    <div className={'setup-task-list setup-task-list--stage-' + current}>{tasks[current].map(([title, description, href]) => <button key={title} className={path === href.split('?')[0] ? 'current' : ''} onClick={() => navigate(href)}><strong>{title} <span aria-hidden="true">→</span></strong><span>{description}</span>{path === href.split('?')[0] && <small>{tr('当前页面', 'Current page')}</small>}</button>)}</div>
    <div className="setup-journey-footer"><span>{cluster ? tr('以下操作始终针对当前集群；保存后可随时从集群管理继续。', 'These actions apply to this cluster. Resume saved settings from Clusters at any time.') : tr('先选择要配置的集群，再进入对应步骤；每个集群分别保存配置。', 'Select a cluster before opening a setup step. Each cluster keeps its own settings.')}</span></div>
  </section>;
}
