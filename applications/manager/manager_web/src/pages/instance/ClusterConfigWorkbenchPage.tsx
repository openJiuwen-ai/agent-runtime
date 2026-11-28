import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { clusterPagePath, type ClusterWorkspaceSection } from '../../utils/clusterNavigation';
import { useClusterSetupProgress } from '../../hooks/useClusterSetupProgress';
import { ClusterWorkspaceNavigation } from '../../components/ClusterWorkspaceNavigation';
import { ClusterReadinessPanel } from './ClusterReadinessPanel';
import { WorkspaceNote } from '../../components/WorkspaceNote';
import { guidedClusterResumeStage, guidedClusterWizardPath } from '../../utils/clusterWizard';
import './ClusterConfigWorkbenchPage.css';

/**
 * The stable home for a cluster. Re-entering always derives the next task from saved data.
 * Layout exception: intentionally full-bleed (no .manager-page) so the setup grid can use the viewport.
 */
export function ClusterConfigWorkbenchPage({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const { navigate, params } = useRouter();
  const state = useClusterSetupProgress(instanceId);
  const open = (section: string) => {
    const [path, search = ''] = section.split('?');
    const [target, context = ''] = clusterPagePath(instanceId, path as ClusterWorkspaceSection, params).split('?');
    const query = new URLSearchParams(context);
    new URLSearchParams(search).forEach((value, key) => query.set(key, value));
    navigate(`${target}${query.size ? `?${query}` : ''}`);
  };
  const data = state.data;
  if (state.loading && !data) {
    return <div className="card">{t('redesign.workbench.loading')}</div>;
  }
  if (state.error || !data) {
    return (
      <div className="card" role="alert">
        <p className="text-danger">{state.error || t('redesign.workbench.loadFailed')}</p>
        <div className="mt-3 flex gap-2">
          <button className="btn" onClick={() => void state.reload()}>{t('redesign.workbench.retry')}</button>
          <button className="btn" onClick={() => navigate('/instances')}>{t('redesign.workbench.returnToClusters')}</button>
        </div>
      </div>
    );
  }
  const steps = [
    {
      section: 'access',
      title: t('redesign.workbench.accessTitle'),
      text: t('redesign.workbench.accessText'),
      summary: t('redesign.workbench.accessSummary', {
        users: data.admission.users,
        orgs: data.admission.orgs,
      }),
      complete: data.admission.total > 0,
    },
    {
      section: 'service-resources',
      title: t('redesign.workbench.runtimeTitle'),
      text: t('redesign.workbench.runtimeText'),
      summary: t('redesign.workbench.runtimeSummary', { count: data.runtime.total }),
      complete: data.runtime.total > 0,
    },
    {
      section: 'agent-resources',
      title: t('redesign.workbench.agentTitle'),
      text: t('redesign.workbench.agentText'),
      summary: t('redesign.workbench.agentSummary', { count: data.agents.total }),
      complete: data.agents.total > 0,
    },
    {
      section: 'status',
      title: t('redesign.workbench.statusTitle'),
      text: t('redesign.workbench.statusText'),
      summary: data.connected
        ? t('redesign.workbench.statusSummaryConnected')
        : t('redesign.workbench.statusSummaryPending'),
      complete: data.connected,
    },
  ];
  const optionalSettings = [
    { id: 'log-masking', title: t('redesign.workbench.logMaskingTitle'), text: t('redesign.workbench.logMaskingText') },
    { id: 'logging', title: t('redesign.workbench.loggingTitle'), text: t('redesign.workbench.loggingText') },
    { id: 'cron-quota', title: t('redesign.workbench.cronQuotaTitle'), text: t('redesign.workbench.cronQuotaText') },
    { id: 'team-quota', title: t('redesign.workbench.teamQuotaTitle'), text: t('redesign.workbench.teamQuotaText') },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ClusterWorkspaceNavigation
        instanceId={instanceId}
        name={data.instance.jiuwenclaw_name}
        section="cluster-config"
        onRefresh={() => void state.reload()}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">{t('redesign.workbench.guidedSetupHint')}</p>
        <button
          type="button"
          className="btn"
          onClick={() => navigate(guidedClusterWizardPath(instanceId, guidedClusterResumeStage(data.nextSection), params))}
        >
          {t('redesign.workbench.continueGuidedSetup')} →
        </button>
      </div>
      <section className="cluster-setup-grid" aria-label={t('redesign.workbench.setupSteps')}>
        {steps.map((item, index) => (
          <article
            className={`card cluster-setup-card ${item.section === data.nextSection ? 'next' : ''}`}
            key={item.section}
          >
            <div className="flex items-center justify-between gap-2">
              <span className={`cluster-setup-card__number ${item.complete ? 'complete' : ''}`}>
                {item.complete ? '✓' : index + 1}
              </span>
              <span className={`pill sm ${item.complete ? 'ok' : ''}`}>
                {item.complete
                  ? (index === 3 ? t('redesign.workbench.connected') : t('redesign.workbench.configured'))
                  : (index === 3 ? t('redesign.workbench.needsCheck') : t('redesign.workbench.needsSetup'))}
              </span>
            </div>
            <h2 className="mt-4 font-semibold">{item.title}</h2>
            <p className="mt-2 text-sm text-muted">{item.text}</p>
            <p className="mt-4 text-xs text-muted">{item.summary}</p>
            <button
              className={`btn mt-4 ${item.section === data.nextSection ? 'primary' : ''}`}
              onClick={() => open(item.section)}
            >
              {item.complete || item.section === 'service-resources'
                ? t('redesign.workbench.reviewEdit')
                : t('redesign.workbench.enterConfig')}
            </button>
          </article>
        ))}
      </section>
      <div className="cluster-workbench-secondary">
        <section className="card cluster-workbench-options">
          <div className="card-header">
            <div>
              <h2>{t('redesign.workbench.optionalTitle')}</h2>
              <p className="text-sm text-muted">{t('redesign.workbench.optionalSubtitle')}</p>
            </div>
            <button className="btn sm" onClick={() => open('other-settings')}>
              {t('redesign.workbench.optionalAll')}
            </button>
          </div>
          <div className="cluster-workbench-options__grid">
            {optionalSettings.map((item) => (
              <button
                className="cluster-workbench-option"
                key={item.id}
                onClick={() => open(`other-settings?setting=${item.id}`)}
              >
                <strong>{item.title} · {t('redesign.workbench.optionalTag')}</strong>
                <span className="text-muted">{item.text}</span>
              </button>
            ))}
          </div>
        </section>
        <ClusterReadinessPanel key={instanceId} data={data} refreshing={state.loading} reload={state.reload} />
      </div>
      <WorkspaceNote title={t('redesign.workbench.noteTitle')}>
        {t('redesign.workbench.noteBody')}
      </WorkspaceNote>
    </div>
  );
}
