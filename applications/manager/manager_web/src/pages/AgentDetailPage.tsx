import { useState } from 'react';
import { agentClusterReturnPath, savedAgentBindingContext, templateFlowQuery, templateReturnPath } from '../utils/agentFlowContext';
import { clusterPagePath } from '../utils/clusterNavigation';
import { useTranslation } from 'react-i18next';
import { AgentTemplateApi } from '../services/api';
import { loadAgentBindings, loadAgentReferences } from '../services/agentWorkspace';
import { useAsync } from '../hooks/useAsync';
import { useRouter } from '../router';
import { AgentTemplateModal } from './templates/AgentTemplateModal';
import { bindingExpiry } from '../utils/agentDraft';
import { formatTime } from '../utils/format';
import { WorkspaceNote } from '../components/WorkspaceNote';
import { AgentExpertPreviewSummary } from '../components/AgentExpertBindingsPreview';

export function AgentDetailPage({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { navigate, params } = useRouter();
  const returnTo = templateReturnPath(params.get('returnTo'));
  const [editing, setEditing] = useState(false);
  const [revision, setRevision] = useState(0);
  const agent = useAsync(() => AgentTemplateApi.get(id), [id, revision]);
  const references = useAsync(async () => loadAgentReferences(await AgentTemplateApi.get(id)), [id, revision]);
  const bindings = useAsync(() => loadAgentBindings(id), [id, revision]);
  const refresh = () => setRevision(r => r + 1);

  if (agent.loading) return <div role="status">{tr('正在读取 Agent…', 'Loading Agent…')}</div>;
  if (agent.error || !agent.data) return <div className="card" role="alert"><p>{agent.error}</p><button className="btn" onClick={() => navigate(returnTo)}>{tr('返回模板管理', 'Return to templates')}</button><button className="btn" onClick={refresh}>{tr('重试', 'Retry')}</button></div>;
  const definition = agent.data;
  const bound = bindings.data?.filter(row => row.resources.length || row.error) || [];
  const bindingPath = (instanceId: string) => {
    const query = savedAgentBindingContext(params, instanceId);
    const target = clusterPagePath(instanceId, 'agent-resources', query);
    return `${target}${target.includes('?') ? '&' : '?'}${new URLSearchParams({ focusTemplate: id })}`;
  };
  const bindTemplate = () => navigate(agentClusterReturnPath(params, id) || `/agent-templates/${encodeURIComponent(id)}/connect${templateFlowQuery(params)}`);

  return <div className="workspace-overview">
    <div className="page-header">
      <div><button className="btn ghost sm" onClick={() => navigate(returnTo)}>← {tr('Agent模板管理', 'Agent templates')}</button><h1 className="page-title">{definition.template_name}</h1><p className="page-subtitle">{definition.description || tr('尚未填写描述', 'No description yet')}</p></div>
      <div className="flex gap-2"><button className="btn" onClick={refresh}>{tr('刷新', 'Refresh')}</button><button className="btn" onClick={() => setEditing(true)}>{tr('编辑模板', 'Edit template')}</button><button className="btn primary" disabled={!definition.enabled} onClick={bindTemplate}>{tr('绑定集群', 'Bind to a cluster')}</button></div>
    </div>
    <section className="card"><dl className="connect-summary"><dt>{tr('模板状态', 'Definition status')}</dt><dd>{definition.enabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled')}</dd><dt>{tr('标签', 'Tags')}</dt><dd>{definition.agent_tags?.join(' · ') || '—'}</dd><dt>{tr('更新时间', 'Updated')}</dt><dd>{formatTime(definition.updated_at)}</dd></dl><details className="mt-4"><summary>{tr('技术标识', 'Technical identifier')}</summary><code>{id}</code></details></section>
    <section className="card"><div className="card-header"><h2>{tr('能力与安全策略', 'Capabilities & policies')}</h2><span className="text-muted text-sm">{tr('定义引用，不代表运行时已加载', 'Definition references, not runtime confirmation')}</span></div>
      {references.loading && <p role="status">{tr('正在解析资源…', 'Resolving resources…')}</p>}
      {references.error && <p role="alert" className="text-danger">{references.error}</p>}
      {references.data?.length === 0 && <p className="overview-empty">{tr('没有配置资源引用', 'No resource references configured')}</p>}
      <div className="agent-reference-list">{references.data?.map((row, index) => <div className="agent-reference" key={`${row.slot}-${row.id}-${index}`}><div><span className="text-muted text-xs">{t(`policies.templateRef.slots.${row.slot}`, { defaultValue: row.slot })}</span><h3>{row.name}</h3>{row.error && <p className="text-danger text-sm" role="alert">{tr('资源读取失败：', 'Resource could not be loaded: ')}{row.error}</p>}</div><span className="tag">{row.dynamic ? tr('运行时解析', 'Resolved at runtime') : row.error ? tr('待检查', 'Inspect') : row.enabled ? tr('已启用', 'Enabled') : tr('已停用', 'Disabled')}</span>{row.path && <a className="btn sm" href={`/manager${row.path}`} target="_blank" rel="noopener noreferrer">{tr('打开资源目录', 'Open catalog')} ↗</a>}</div>)}</div>
    </section>
    <AgentExpertPreviewSummary templateId={id} />
    <section className="card"><div className="card-header"><h2>{tr('集群绑定与使用范围', 'Instance bindings & access')}</h2></div><p className="text-muted text-sm">{tr('以下为已保存的绑定。实例准入和运行规格需要分别检查。', 'Saved bindings are shown below. Check admission and runtime profiles separately.')}</p>
      {bindings.loading && <p role="status">{tr('正在检查实例绑定…', 'Checking instance bindings…')}</p>}
      {bindings.error && <p role="alert" className="text-danger">{bindings.error}</p>}
      {bindings.data && !bound.length && <div className="overview-empty">{tr('尚未绑定集群', 'Not bound to any instance')}</div>}
      {bound.map(({ instance, resources, error }) => <div className="agent-binding" key={instance.jiuwenclaw_id}><div className="flex justify-between gap-2"><h3>{instance.jiuwenclaw_name}</h3><button className="btn sm" onClick={() => navigate(bindingPath(instance.jiuwenclaw_id))}>{tr('管理绑定', 'Manage bindings')} →</button></div>{error ? <p className="text-danger" role="alert">{error}</p> : resources.map(resource => <div key={resource.resource_id} className="binding-resource"><strong>{resource.resource_name || definition.template_name}</strong><span className="tag">{resource.enabled ? tr('绑定已启用', 'Binding enabled') : tr('绑定已停用', 'Binding disabled')}</span><p className="text-sm text-muted">{bindingExpiry(resource.expires_at) === 'expired' ? tr('已过期：', 'Expired: ') : bindingExpiry(resource.expires_at) === 'unknown' ? tr('有效期格式异常：', 'Invalid expiry: ') : tr('有效期至：', 'Expires: ')}{resource.expires_at ? formatTime(resource.expires_at) : tr('长期有效', 'No expiry')}</p><details><summary>{tr('查看授权条件', 'View access conditions')}</summary><pre>{typeof resource.match_expr === 'string' ? resource.match_expr || tr('实例内已准入的全体用户', 'All admitted users') : JSON.stringify(resource.match_expr)}</pre></details></div>)}</div>)}
    </section>
    <AgentTemplateModal open={editing} template={definition} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />
    <WorkspaceNote title={tr('Agent模板与集群绑定分开配置', 'Agent definition and cluster activation are separate steps')}>
      {tr('当前页维护 Agent 的能力组合；要让它在实际环境中可用，请回到集群配置工作台完成准入、运行资源和 Agent 绑定。', 'This page maintains the Agent capability composition. Return to the cluster workspace to configure admission, runtime resources and the binding that makes it available.')}
    </WorkspaceNote>
  </div>;
}
