import { useTranslation } from 'react-i18next';
import { A2AAccessPoliciesPage } from './A2AAccessPoliciesPage';
import { A2AOutboundTemplatesPage } from './A2AOutboundTemplatesPage';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { useRouter } from '../../router';

/** A2A 管理页：agents / policies 两个页签；页签状态同步到 ?tab= 以支持外链直达。 */
export function A2AManagementPage() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const { params, navigate } = useRouter();
  const tab = params.get('tab') === 'policies' ? 'policies' : 'agents';
  const setTab = (next: 'agents' | 'policies') => navigate(`/a2a-management${next === 'policies' ? '?tab=policies' : ''}`);

  const header = (
    <div className="min-w-[7.5rem] max-w-[20rem] shrink-0 sm:max-w-[32rem]">
      <div className="page-title truncate" title={t('a2a.managementTitle')}>{t('a2a.managementTitle')}</div>
      <div className="page-subtitle truncate" title={t('a2a.managementSubtitle')}>{t('a2a.managementSubtitle')}</div>
    </div>
  );
  const footer = (
    <WorkspaceNote title={t('a2a.managementTitle')}>
      {t('a2a.managementSubtitle')}
      {zh ? '。' : '. '}
      {zh
        ? '保存后，在 Agent 或集群配置中引用；修改已被引用的资源前，请先查看关联模板和集群。'
        : 'Reference saved resources from an Agent or cluster configuration. Review linked templates and clusters before changing a referenced resource.'}
    </WorkspaceNote>
  );
  const tabs = (
    <div className="tabs-bar max-w-full overflow-x-auto" role="tablist" aria-label={t('a2a.managementTitle')}>
      <button type="button" role="tab" aria-selected={tab === 'agents'} className={`tab ${tab === 'agents' ? 'active' : ''}`} onClick={() => setTab('agents')}>
        A2A Agent
      </button>
      <button type="button" role="tab" aria-selected={tab === 'policies'} className={`tab ${tab === 'policies' ? 'active' : ''}`} onClick={() => setTab('policies')}>
        {t('a2a.accessPolicies')}
      </button>
    </div>
  );

  return (
    <>
      {tab === 'agents'
        ? <A2AOutboundTemplatesPage embedded header={header} tabs={tabs} />
        : <A2AAccessPoliciesPage header={header} tabs={tabs} />}
      {footer}
    </>
  );
}
