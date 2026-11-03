import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import '../instanceAccessPanel/instanceBinding.css';

interface Props {
  kind: 'runtime' | 'agent';
  candidates: { id: string; label: string; enabled?: boolean }[];
  value: string;
  onChange: (id: string) => void;
  loading: boolean;
  error: string | null;
  disabled?: boolean;
  onReload: () => void;
  onManage: () => void;
}

/** Match the admission picker, with a single template choice per resource binding. */
export function ClusterTemplatePicker({ kind, candidates, value, onChange, loading, error, disabled, onReload, onManage }: Props) {
  const { i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const radioName = useId();
  const [query, setQuery] = useState('');
  const subject = kind === 'runtime' ? tr('运行规格', 'runtime profiles') : tr('Agent 模板', 'Agent templates');
  const page = kind === 'runtime' ? tr('运行规格', 'runtime profiles') : tr('Agent模板管理', 'Agent templates');
  const returnLabel = kind === 'runtime' ? tr('返回集群运行资源', 'Return to cluster runtime resources') : tr('返回集群配置', 'Return to cluster');
  const keyword = query.trim().toLowerCase();
  const filtered = candidates.filter(item => `${item.label} ${item.id}`.toLowerCase().includes(keyword));
  const selected = candidates.find(item => item.id === value && item.enabled !== false);
  const manageButton = (primary = false) => <button type="button" className={`btn${primary ? ' primary' : ''}`} onClick={onManage}>{tr(`前往${page}`, `Open ${page}`)} →</button>;

  if (loading) return <div className="admission-picker-empty" role="status">{tr(`正在读取可选${subject}…`, `Loading available ${subject}…`)}</div>;
  if (error) return <div className="admission-picker-empty" role="alert"><strong>{tr(`无法读取${subject}`, `Could not load ${subject}`)}</strong><p>{error}</p><button type="button" className="btn" onClick={onReload}>{tr('重试', 'Retry')}</button></div>;
  if (!candidates.some(item => item.enabled !== false)) return <div className="admission-picker-empty">
    <span className="admission-picker-empty__icon" aria-hidden="true">＋</span>
    <h3>{candidates.length === 0 ? tr(`还没有${subject}，先到管理页创建`, `Create ${subject} to get started`) : tr(`暂无可用${subject}，请先启用模板`, `Enable a template to use ${subject}`)}</h3>
    <p>{tr(`在管理页创建或启用${subject}后，点击“${returnLabel}”即可继续添加。已填写的内容会保留。`, `Create or enable ${subject}, then use “${returnLabel}” to continue. Your draft is kept.`)}</p>
    {manageButton(true)}
  </div>;

  return <div>
    <div className="admission-picker-toolbar"><div><strong>{tr(`选择要绑定的${subject}`, `Select ${subject}`)}<span className="text-danger ml-0.5" aria-hidden="true">*</span></strong><p>{tr(`找不到${subject}？可先到管理页创建或启用，再返回继续。`, 'Missing a template? Create or enable it in the directory, then return here.')}</p></div>{manageButton()}</div>
    <input className="input w-full mb-2" aria-label={tr(`搜索${subject}`, `Search ${subject}`)} placeholder={tr(`搜索${subject}名称或 ID`, `Search ${subject} by name or ID`)} value={query} onChange={event => setQuery(event.target.value)} />
    <div className="admission-picker-list" role="radiogroup" aria-label={tr(`选择${subject}`, `Select ${subject}`)}>
      {filtered.map(item => <label key={item.id}>
        <input type="radio" name={radioName} value={item.id} checked={value === item.id} disabled={disabled || item.enabled === false} onChange={() => onChange(item.id)} />
        <span className="min-w-0"><span className="block break-words">{item.label}{item.enabled === false && <span className="tag ml-2">{tr('已停用', 'Disabled')}</span>}</span><span className="block text-xs text-muted mono break-all">{item.id}</span></span>
      </label>)}
      {filtered.length === 0 && <div className="admission-picker-empty"><p>{tr(`没有匹配“${query}”的${subject}`, `No ${subject} match “${query}”`)}</p><button type="button" className="btn sm" onClick={() => setQuery('')}>{tr('清空搜索', 'Clear search')}</button></div>}
    </div>
    <p className="text-xs text-muted mt-2">{tr(`已选择 ${selected ? 1 : 0} 项 · 前往管理页时保留当前选择和绑定设置`, `${selected ? 1 : 0} selected · Your selection and binding settings are kept while you visit the directory`)}</p>
  </div>;
}
