import { useTranslation } from 'react-i18next';
import { useAsync } from '../hooks/useAsync';
import { loadAgentExpertPackages } from '../services/agentWorkspace';

/** Expert capabilities come from the persisted Agent template reference. */
export function AgentExpertBindingSummary({ templateId }: { templateId: string }) {
  const { t, i18n } = useTranslation();
  const tr = (cn: string, en: string) => i18n.language.startsWith('zh') ? cn : en;
  const packages = useAsync(() => loadAgentExpertPackages(templateId), [templateId]);

  if (packages.loading) return <p className="text-xs text-muted" role="status">{tr('正在读取专家包配置…', 'Loading expert package configuration…')}</p>;
  if (packages.error) return <div className="flex items-center gap-2 text-sm mt-3" role="alert">
    <span className="text-danger">{t('errors.loadFailed', { detail: packages.error })}</span>
    <button type="button" className="btn sm" onClick={() => void packages.reload()}>{t('common.refresh')}</button>
  </div>;
  if (!packages.data?.length) return null;

  return <section className="card !p-4 mt-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <strong className="text-sm">{tr('随 Agent 发布的专家包', 'Expert packages published with this Agent')}</strong>
      <a className="btn sm" href="/manager/expert-package-templates" target="_blank" rel="noopener noreferrer">{tr('查看专家包模板', 'View expert package templates')} ↗</a>
    </div>
    <div className="flex flex-wrap gap-2 mt-2">
      {packages.data.map((item, index) => <div key={`${item.id}-${index}`}>
        <span className="tag">{item.name} · {item.dynamic ? tr('运行时解析', 'Resolved at runtime') : item.error ? tr('待检查', 'Inspect') : item.enabled ? t('common.enabled') : t('common.disabled')}</span>
        {item.error && <p className="text-xs text-danger mt-1" role="alert">{t('errors.loadFailed', { detail: item.error })}</p>}
      </div>)}
    </div>
    <p className="text-xs text-muted mt-2">{tr('专家能力由 Agent 模板引用，在集群发布该 Agent 时一起下发。', 'Expert capabilities are referenced by the Agent template and delivered when the Agent is published to the cluster.')}</p>
  </section>;
}
