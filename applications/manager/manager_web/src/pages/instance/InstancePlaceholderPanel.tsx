import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { clusterPagePath, type ClusterWorkspaceSection } from '../../utils/clusterNavigation';

/** 集群详情中尚未接入数据接口的模块，仍提供完整的上下文和下一步入口。 */
export function InstancePlaceholderPanel({
  titleKey,
  subtitleKey,
  emptyKey = 'instanceDetail.placeholder.empty',
  instanceId,
}: {
  titleKey: string;
  subtitleKey: string;
  emptyKey?: string;
  instanceId?: string;
}) {
  const { t } = useTranslation();
  const { navigate, params } = useRouter();
  const title = t(titleKey);
  const subtitle = t(subtitleKey);
  const isAudit = titleKey.includes('.audit');
  const isCost = titleKey.includes('.cost');
  const isTokenQuota = titleKey.includes('.tokenQuota');
  const moduleLabel = isAudit
    ? t('instanceDetail.placeholder.auditLabel')
    : isCost
      ? t('instanceDetail.placeholder.costLabel')
      : isTokenQuota
        ? t('instanceDetail.placeholder.tokenQuotaLabel')
        : title;
  const moduleDescription = isAudit
    ? t('instanceDetail.placeholder.auditDescription')
    : isCost
      ? t('instanceDetail.placeholder.costDescription')
      : isTokenQuota
        ? t('instanceDetail.placeholder.tokenQuotaDescription')
        : subtitle;
  const go = (section: ClusterWorkspaceSection) => instanceId && navigate(clusterPagePath(instanceId, section, params));

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header items-start">
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted">
            {t('instanceDetail.placeholder.eyebrow', { module: moduleLabel })}
          </div>
          <div className="page-title text-base mt-1">{title}</div>
          <div className="page-subtitle">{moduleDescription}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn sm" onClick={() => go('status')} disabled={!instanceId}>
            {t('instanceDetail.placeholder.viewStatus')}
          </button>
          <button type="button" className="btn primary sm" onClick={() => go('cluster-config')} disabled={!instanceId}>
            {t('instanceDetail.placeholder.backToWorkbench')}
          </button>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(16rem,0.7fr)]">
        <section className="card instance-placeholder-card">
          <div className="card-header">
            <div>
              <div className="card-title">{moduleLabel}</div>
              <p className="text-sm text-muted mt-1">{moduleDescription}</p>
            </div>
            <span className="tag">{t('instanceDetail.placeholder.backendPending')}</span>
          </div>
          <div className="instance-placeholder-state">
            <div className="instance-placeholder-state__icon" aria-hidden="true">{isAudit ? '◌' : isCost ? '↗' : '◫'}</div>
            <div>
              <h3>{t(emptyKey)}</h3>
              <p>{t('instanceDetail.placeholder.moduleUnavailable')}</p>
            </div>
          </div>
        </section>
        <aside className="card instance-placeholder-side">
          <div className="card-title">{t('instanceDetail.placeholder.whatYouCanDo')}</div>
          <p className="text-sm text-muted mt-1">{t('instanceDetail.placeholder.whatYouCanDoHint')}</p>
          <div className="flex flex-col gap-2 mt-4">
            <button type="button" className="btn w-full justify-between" onClick={() => go('access')} disabled={!instanceId}>
              {t('instanceDetail.placeholder.configureAdmission')} <span>→</span>
            </button>
            <button type="button" className="btn w-full justify-between" onClick={() => go('service-resources')} disabled={!instanceId}>
              {t('instanceDetail.placeholder.configureRuntime')} <span>→</span>
            </button>
            <button type="button" className="btn w-full justify-between" onClick={() => go('agent-resources')} disabled={!instanceId}>
              {t('instanceDetail.placeholder.bindAgent')} <span>→</span>
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
