import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { InstanceApi, PackageAllowlistApi } from '../../services/api';
import { InstanceExpertPackagesTab } from '../instance/instanceResourcePanel/InstanceExpertPackagesTab';
import { ResourceWorkspaceFrame } from '../../components/ResourceWorkspaceFrame';
import { expertPreviewExamples } from '../../components/AgentExpertBindingsPreview';
import { useRouter } from '../../router';
import { collectPages } from '../../utils/overview';
import { isMockInstanceId } from '../../config/mockManager';
import { expertResourceReturnPath } from '../../utils/expertResourceNavigation';

export function ExpertPackagesPage() {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const { params, navigate } = useRouter();
  const clusters = useAsync(() => collectPages((page, page_size) => InstanceApi.list({ page, page_size, sort_by: 'jiuwenclaw_name', sort_order: 'asc' })), []);
  const realClusters = (clusters.data ?? []).filter(cluster => !isMockInstanceId(cluster.jiuwenclaw_id));
  const pickedId = params.get('cluster') || params.get('clusterId') || '';
  const selectedId = realClusters.some(cluster => cluster.jiuwenclaw_id === pickedId) ? pickedId : '';
  const publishing = params.get('view') === 'publish';
  const demo = !publishing && params.get('source') === 'demo';
  const kind = params.get('kind') === 'agent_group' ? 'agent_group' : 'agent_template';
  const search = params.get('search') || '';
  const returnPath = expertResourceReturnPath(params.get('returnTo'));
  const catalog = useAsync(() => !publishing && !demo && selectedId ? PackageAllowlistApi.catalog(selectedId) : Promise.resolve({ items: [], error: '' }), [selectedId, publishing, demo]);
  const catalogItems = demo ? expertPreviewExamples : !catalog.loading && !catalog.error ? catalog.data?.items ?? [] : [];
  const rows = catalogItems.filter(item => item.kind === kind && `${item.display_name} ${item.id}`.toLowerCase().includes(search.toLowerCase().trim()));
  const updateQuery = (values: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params);
    next.delete('clusterId');
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    navigate(`/expert-packages${next.size ? `?${next}` : ''}`, { replace });
  };
  const templateTarget = returnPath?.startsWith('/agent-templates') ? returnPath : '/agent-templates';
  const unavailable = pickedId && !selectedId && !clusters.loading && !clusters.error;

  return <ResourceWorkspaceFrame>
    {returnPath && <div><button className="btn ghost sm" onClick={() => navigate(returnPath)}>{returnPath.startsWith('/instances/') ? tr('← 返回来源集群的 Agent绑定', '← Back to source cluster Agent bindings') : tr('← 返回 Agent模板', '← Back to Agent template')}</button></div>}
    <div className="page-header"><div><h1 className="page-title">{tr('专家与专家包', 'Experts & packages')}</h1><p className="page-subtitle">{tr('浏览专家能力，在 Agent 模板中组合，再随模板绑定到集群。', 'Browse expert capabilities, compose them in an Agent template, then bind the template to a cluster.')}</p></div><button className="btn primary" onClick={() => navigate(templateTarget)}>{tr('前往 Agent模板', 'Open Agent templates')} →</button></div>
    <div className="tabs-bar" role="tablist" aria-label={tr('专家资源管理', 'Expert resource management')}>
      <button type="button" role="tab" aria-selected={!publishing} className={`tab ${!publishing ? 'active' : ''}`} onClick={() => updateQuery({ view: null })}>{tr('资源目录', 'Resource catalog')}</button>
      <button type="button" role="tab" aria-selected={publishing} className={`tab ${publishing ? 'active' : ''}`} onClick={() => updateQuery({ view: 'publish' })}>{tr('集群发布（现有功能）', 'Cluster publication (existing)')}</button>
    </div>
    {unavailable && <p className="text-sm text-muted" role="status">{isMockInstanceId(pickedId) ? tr('来源是演示集群。请选择界面演示查看资源，或选择真实集群连接读取目录。', 'The source is a demo cluster. Choose UI examples or a real cluster connection.') : tr('原集群不存在或当前不可访问，请重新选择目录来源。', 'The original cluster is unavailable. Choose another catalog source.')}</p>}
    {publishing ? <InstanceExpertPackagesTab key={selectedId} instanceId={selectedId} clusters={realClusters.map(cluster => ({ id: cluster.jiuwenclaw_id, name: cluster.jiuwenclaw_name }))} clustersLoading={clusters.loading} clustersError={clusters.error} onClusterChange={id => updateQuery({ cluster: id || null, source: null })} /> : <>
      <section className="card">
        <div className="flex flex-wrap items-end gap-3"><label className="flex-1 min-w-48"><span className="label">{tr('资源目录来源', 'Catalog source')}</span><select className="select" aria-label={tr('资源目录来源', 'Catalog source')} value={demo ? 'demo' : selectedId} onChange={event => updateQuery({ source: event.target.value === 'demo' ? 'demo' : null, cluster: event.target.value === 'demo' ? pickedId || null : event.target.value || null })}>
          <option value="">{tr('选择已接入的集群目录', 'Choose a connected cluster catalog')}</option><option value="demo">{tr('界面演示（仅预览）', 'UI examples (preview only)')}</option>
          {realClusters.map(cluster => <option value={cluster.jiuwenclaw_id} key={cluster.jiuwenclaw_id}>{cluster.jiuwenclaw_name}</option>)}
        </select></label><button type="button" className="btn" disabled={clusters.loading || catalog.loading} onClick={() => { void clusters.reload(); void catalog.reload(); }}>{tr('刷新目录', 'Refresh catalog')}</button></div>
        <p className="text-xs text-muted mt-2">{tr('当前通过集群连接读取 Hub 目录；目录来源不限制模板在哪些集群使用。模板级专家绑定处于前端预览阶段。', 'Hub catalogs currently require a cluster connection. The catalog source does not restrict where templates are used. Template-level expert bindings are a frontend preview.')}</p>
        {clusters.error && <p className="text-danger text-sm mt-2" role="alert">{clusters.error}</p>}
      </section>
      <section className="card !p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 p-4"><div className="tabs-bar !mb-0" role="tablist" aria-label={tr('专家资源类型', 'Expert resource types')}>
          <button type="button" role="tab" aria-selected={kind === 'agent_template'} className={`tab ${kind === 'agent_template' ? 'active' : ''}`} onClick={() => updateQuery({ kind: null })}>{tr('专家', 'Experts')}</button>
          <button type="button" role="tab" aria-selected={kind === 'agent_group'} className={`tab ${kind === 'agent_group' ? 'active' : ''}`} onClick={() => updateQuery({ kind: 'agent_group' })}>{tr('专家包（专家团）', 'Packages (expert teams)')}</button>
        </div><input className="input !w-auto" aria-label={tr('搜索专家资源', 'Search expert resources')} placeholder={tr('搜索名称或 ID', 'Search name or ID')} value={search} onChange={event => updateQuery({ search: event.target.value || null }, true)} /></div>
        {!demo && !selectedId ? <div className="overview-empty">{tr('选择目录来源后查看可用资源。尚未接入 Hub 时，可以选择界面演示体验模板绑定。', 'Choose a catalog source to view resources. UI examples are available before a Hub is connected.')}</div> : !demo && catalog.loading ? <div className="overview-empty" role="status">{tr('正在读取目录…', 'Loading catalog…')}</div> : !demo && (catalog.error || catalog.data?.error) ? <div className="overview-empty" role="alert"><p className="text-danger">{tr('目录暂时不可用：', 'Catalog unavailable: ')}{catalog.error || catalog.data?.error}</p><p>{tr('请检查该集群的 Hub 连接，或切换到界面演示。', 'Check the cluster Hub connection or choose UI examples.')}</p></div> : <div className="grid gap-3 p-4 md:grid-cols-2">
          {rows.map(item => <article className="card expert-catalog-card" key={`${item.kind}:${item.id}`}><div className="flex justify-between items-start gap-2"><h2 className="text-base font-semibold">{item.display_name || item.id}</h2><span className="tag">{demo ? tr('演示资源', 'Demo resource') : tr('Hub 资源', 'Hub resource')}</span></div><p className="text-xs mono text-muted mt-2 break-all">{item.id}</p><dl className="flex gap-5 mt-4 text-sm"><div><dt className="text-muted">{tr('类型', 'Type')}</dt><dd>{kind === 'agent_template' ? tr('专家', 'Expert') : tr('专家包（专家团）', 'Package (expert team)')}</dd></div><div><dt className="text-muted">{tr('版本', 'Version')}</dt><dd>{item.version || '—'}</dd></div></dl><p className="text-xs text-muted mt-4">{tr('在 Agent 模板的“专家与专家包”中选择此资源。', 'Select this resource in the Agent template expert section.')}</p></article>)}
          {!rows.length && <p className="overview-empty md:col-span-2">{tr('没有匹配的资源', 'No matching resources')}</p>}
        </div>}
      </section>
    </>}
  </ResourceWorkspaceFrame>;
}
