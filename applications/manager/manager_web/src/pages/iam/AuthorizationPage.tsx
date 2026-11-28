import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { WorkspaceNote } from '../../components/WorkspaceNote';

type AuthorizationEntry = {
  title: string;
  description: string;
  href: string;
  action: string;
};

/**
 * 授权是访问授权目录的总入口。
 *
 * 具体的用户、组织、角色仍由各自页面管理；这个页面只把“谁、通过什么角色、
 * 可以进入哪个集群”这条关系串起来，避免把“授权”误做成重复的角色列表。
 */
export function AuthorizationPage() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const entries: AuthorizationEntry[] = [
    {
      title: t('iam.authorizationUsersTitle'),
      description: t('iam.authorizationUsersText'),
      href: '/users',
      action: t('iam.authorizationUsersAction'),
    },
    {
      title: t('iam.authorizationOrgsTitle'),
      description: t('iam.authorizationOrgsText'),
      href: '/orgs',
      action: t('iam.authorizationOrgsAction'),
    },
    {
      title: t('iam.authorizationRolesTitle'),
      description: t('iam.authorizationRolesText'),
      href: '/roles',
      action: t('iam.authorizationRolesAction'),
    },
    {
      title: t('iam.authorizationInstancesTitle'),
      description: t('iam.authorizationInstancesText'),
      href: '/instances',
      action: t('iam.authorizationInstancesAction'),
    },
  ];

  return (
    <div className="authorization-workspace flex min-w-0 flex-col gap-5">
      <div className="page-header w-full min-w-0 flex-wrap items-start gap-y-3">
        <div className="min-w-0">
          <div className="page-title">{t('iam.authorizationTitle')}</div>
          <div className="page-subtitle">
            {t('iam.authorizationSubtitle')}
          </div>
        </div>
      </div>

      <div className="authorization-columns">
        <section className="card authorization-panel">
          <div className="card-header">
            <div>
              <h2>{t('iam.authorizationPrepare')}</h2>
              <p className="text-sm text-muted">{t('iam.authorizationPrepareHint')}</p>
            </div>
            <span className="pill sm muted">{t('iam.authorizationOptional')}</span>
          </div>
          <div className="authorization-check-list">
            {entries.slice(0, 3).map((entry) => (
              <div className="authorization-check" key={entry.href}>
                <span className="statusDot" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <strong>{entry.title}</strong>
                  <p>{entry.description}</p>
                </div>
                <button className="btn sm" type="button" onClick={() => navigate(entry.href)}>{entry.action} →</button>
              </div>
            ))}
          </div>
        </section>
        <section className="card authorization-panel">
          <div className="card-header">
            <div>
              <h2>{t('iam.authorizationEffect')}</h2>
              <p className="text-sm text-muted">{t('iam.authorizationEffectHint')}</p>
            </div>
            <button className="btn primary sm" type="button" onClick={() => navigate('/instances')}>
              {t('iam.authorizationViewClusters')}
            </button>
          </div>
          <div className="authorization-check-list">
            <div className="authorization-check">
              <span className="statusDot" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <strong>{t('iam.authorizationClusterAdmission')}</strong>
                <p>{t('iam.authorizationClusterAdmissionText')}</p>
              </div>
              <button className="btn sm" type="button" onClick={() => navigate('/instances')}>
                {t('iam.authorizationSelectCluster')} →
              </button>
            </div>
            <div className="authorization-check">
              <span className="statusDot" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <strong>{t('iam.authorizationAgentScope')}</strong>
                <p>{t('iam.authorizationAgentScopeText')}</p>
              </div>
              <button className="btn sm" type="button" onClick={() => navigate('/instances')}>
                {t('iam.authorizationSelectCluster')} →
              </button>
            </div>
          </div>
        </section>
      </div>

      <WorkspaceNote
        title={t('redesign.authorizationNoteTitle')}
        action={(
          <button className="btn primary sm" type="button" onClick={() => navigate('/instances')}>
            {t('redesign.authorizationNoteAction')}
          </button>
        )}
      >
        {t('redesign.authorizationNoteText')}
      </WorkspaceNote>

    </div>
  );
}
