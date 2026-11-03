import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { useAsync } from '../hooks/useAsync';
import { InstanceApi, PackageAllowlistApi } from '../services/api';
import { collectPages } from '../utils/overview';
import { readAgentExpertDraft, type AgentExpertSelection } from '../utils/agentExpertDraft';

import './AgentCapabilityCards.css';

const keyOf = (item: AgentExpertSelection) => `${item.kind}:${item.id}`;

/** UI contract preview only. Never serialize these selections into template_ref or allowlists. */
export function AgentExpertBindingsPreview({ value, onChange, disabled = false, returnTo = '/agent-templates', onSavePreview }: {
  value: AgentExpertSelection[];
  onChange: (value: AgentExpertSelection[]) => void;
  disabled?: boolean;
  returnTo?: string;
  onSavePreview?: () => void;
}) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [expanded, setExpanded] = useState(false);
  const [source, setSource] = useState('');
  const [kind, setKind] = useState<'agent_template' | 'agent_group'>('agent_template');
  const [search, setSearch] = useState('');
  const clusters = useAsync(() => expanded ? collectPages((page, page_size) => InstanceApi.list({ page, page_size })) : Promise.resolve([]), [expanded]);
  const validSource = clusters.data?.some(cluster => cluster.jiuwenclaw_id === source) ? source : '';
  const catalog = useAsync(() => validSource ? PackageAllowlistApi.catalog(validSource) : Promise.resolve({ items: [], error: '' }), [validSource]);
  const items = validSource && !catalog.loading && !catalog.error ? catalog.data?.items ?? [] : [];
  const visible = items.filter(item => item.kind === kind && `${item.display_name} ${item.id}`.toLowerCase().includes(search.trim().toLowerCase()));
  const selected = new Set(value.map(keyOf));
  const toggle = (item: AgentExpertSelection) => onChange(selected.has(keyOf(item)) ? value.filter(row => keyOf(row) !== keyOf(item)) : [...value, item]);
  const query = new URLSearchParams({ returnTo });
  if (validSource) query.set('cluster', validSource);

  return <section className="agent-capability-card agent-expert-preview">
    <div className="agent-capability-card__header">
      <div className="agent-capability-card__heading"><strong>{tr('专家与专家包 · 可选', 'Experts & packages · Optional')}</strong><span className="tag ml-2">{tr('绑定预览', 'Binding preview')}</span>
        <p className="text-xs text-muted mt-1">{tr('选择此 Agent 计划使用的专家能力；模板级绑定待后端接入。', 'Choose expert capabilities for this Agent. The planned flow uses them with the template when bound to a cluster.')}</p>
      </div>
      <a className="btn sm agent-capability-card__link" href={`/manager/expert-packages?${query}`} target="_blank" rel="noopener noreferrer">{tr('前往专家与专家包', 'Open experts & packages')} ↗</a>
    </div>
    <p className="text-xs text-muted mt-2">{tr('待后端接口接入：当前选择仅作为本浏览器中的模板预览保存，不会下发或改变集群授权。', 'Backend integration pending: selections are saved as a browser-local template preview only and do not change cluster grants.')}</p>
    <div className="agent-expert-preview__selection"><span className="text-xs text-muted">{tr(`已选择 ${value.length} 项`, `${value.length} selected`)}</span><button type="button" className="btn sm" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? tr('收起选择', 'Hide selection') : tr('选择资源', 'Select resources')}</button></div>
    {value.length > 0 && <div className="flex flex-wrap gap-2 mt-3">{value.map(item => <span className="tag" key={keyOf(item)}>{item.display_name || item.id}<button type="button" disabled={disabled} className="ml-2" aria-label={tr('移除 ', 'Remove ') + (item.display_name || item.id)} onClick={() => onChange(value.filter(row => keyOf(row) !== keyOf(item)))}>×</button></span>)}</div>}
    {onSavePreview && <button type="button" disabled={disabled} className="btn sm mt-3" onClick={onSavePreview}>{tr('保存绑定预览', 'Save binding preview')}</button>}
    {expanded && <div className="mt-4 flex flex-col gap-3">
      <div className="flex flex-wrap gap-2 items-center">
        <label className="flex-1 min-w-48"><span className="label">{tr('资源目录来源', 'Catalog source')}</span><select className="select" aria-label={tr('专家目录来源', 'Expert catalog source')} value={source} onChange={event => setSource(event.target.value)}>
          <option value="">{tr('选择已接入的集群目录', 'Choose a connected cluster catalog')}</option>
          {(clusters.data ?? []).map(cluster => <option key={cluster.jiuwenclaw_id} value={cluster.jiuwenclaw_id}>{cluster.jiuwenclaw_name}</option>)}
        </select></label>
        <button type="button" className="btn sm self-end" disabled={catalog.loading || clusters.loading} onClick={() => { void clusters.reload(); void catalog.reload(); }}>{tr('刷新', 'Refresh')}</button>
      </div>
      <p className="text-xs text-muted">{tr('当前通过集群连接读取 Hub 目录；此处的来源只用于读取资源，不决定模板在哪个集群生效。', 'The current API reads Hub catalogs through a cluster connection. This source does not determine where the template is used.')}</p>
      {clusters.error && <p role="alert" className="text-danger text-sm">{clusters.error}</p>}
      {source && <>
        <div className="tabs-bar" role="tablist" aria-label={tr('专家资源类型', 'Expert resource types')}>
          <button type="button" role="tab" aria-selected={kind === 'agent_template'} className={`tab ${kind === 'agent_template' ? 'active' : ''}`} onClick={() => setKind('agent_template')}>{tr('专家', 'Experts')}</button>
          <button type="button" role="tab" aria-selected={kind === 'agent_group'} className={`tab ${kind === 'agent_group' ? 'active' : ''}`} onClick={() => setKind('agent_group')}>{tr('专家包（专家团）', 'Packages (expert teams)')}</button>
        </div>
        <input className="input" aria-label={tr('搜索专家资源', 'Search expert resources')} placeholder={tr('搜索名称或 ID', 'Search name or ID')} value={search} onChange={event => setSearch(event.target.value)} />
        {catalog.loading ? <p role="status">{tr('读取目录中…', 'Loading catalog…')}</p> : (catalog.error || catalog.data?.error) ? <p role="alert" className="text-sm text-danger">{tr('目录暂时不可用：', 'Catalog unavailable: ')}{catalog.error || catalog.data?.error}</p> : <div className="max-h-56 overflow-auto flex flex-col gap-2">
          {visible.map(item => <label className="card !p-3 flex items-center gap-3 cursor-pointer" key={keyOf(item)}><input type="checkbox" disabled={disabled} checked={selected.has(keyOf(item))} onChange={() => toggle(item)} /><span className="flex-1 min-w-0"><strong className="text-sm">{item.display_name || item.id}</strong><span className="block text-xs mono text-muted break-all">{item.id}</span></span><span className="text-xs text-muted">{item.version}</span></label>)}
          {!visible.length && <p className="overview-empty">{tr('没有匹配的资源', 'No matching resources')}</p>}
        </div>}
      </>}
    </div>}
  </section>;
}

export function AgentExpertPreviewSummary({ templateId }: { templateId: string }) {
  const { user } = useAuth();
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const [, refresh] = useState(0);
  useEffect(() => {
    const onRefresh = () => refresh(value => value + 1);
    window.addEventListener('storage', onRefresh);
    window.addEventListener('focus', onRefresh);
    return () => { window.removeEventListener('storage', onRefresh); window.removeEventListener('focus', onRefresh); };
  }, []);
  let items: AgentExpertSelection[] = [];
  try { items = readAgentExpertDraft(window.localStorage, user?.user_id || '', templateId); } catch { /* Storage may be unavailable. */ }
  if (!items.length) return null;
  return <div className="card !p-4 mt-3"><strong className="text-sm">{tr('模板专家绑定预览', 'Template expert binding preview')}</strong><div className="flex flex-wrap gap-2 mt-2">{items.map(item => <span className="tag" key={keyOf(item)}>{item.display_name || item.id}</span>)}</div><p className="text-xs text-muted mt-2">{tr('此选择仅保存在本浏览器。后端接入后随模板生效；当前绑定集群不会下发这些专家资源。', 'Browser-local preview. Once supported by the backend these resources will follow the template; cluster binding currently does not deploy them.')}</p></div>;
}
