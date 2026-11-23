import { Fragment, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Empty } from '../../../components/Empty';
import { useAsync } from '../../../hooks/useAsync';
import { TeamQuotaApi } from '../../../services/api';
import type { TeamQuotaUsage } from '../../../types';

export function TeamQuotaUsageTab({ instanceId, refreshKey = 0 }: { instanceId: string; refreshKey?: number }) {
  const { t } = useTranslation();
  const { data, loading, error, reload } = useAsync(() => TeamQuotaApi.listUsage(instanceId), [instanceId, refreshKey]);
  const items = data?.items ?? [];
  const orgs = data?.orgs ?? [];
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const rowKey = (row: TeamQuotaUsage) => `${row.user_id}/${row.group_id}/${row.bot_id}`;

  return (
    <div className="quota-section__content">
      <div className="page-header quota-section__header">
        <div className="quota-section__heading">
          <h5 className="quota-section__title">{t('instanceDetail.teamQuota.tabs.usage')}</h5>
          <p className="quota-section__description">{t('instanceDetail.teamQuota.usage.subtitle')}</p>
        </div>
        <div className="quota-section__actions">
          <button className="btn sm" onClick={() => void reload()}>
            {t('common.refresh')}
          </button>
        </div>
      </div>

      {!loading && !error && orgs.length > 0 ? (
        <div className="card manager-page-table !p-0">
          <div className="px-4 py-3 text-sm font-medium">{t('instanceDetail.teamQuota.usage.orgsTitle')}</div>
          <div className="overflow-x-auto">
            <table className="table w-max min-w-full">
              <thead>
                <tr>
                  <th>{t('instanceDetail.teamQuota.usage.groupId')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.used')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.limit')}</th>
                </tr>
              </thead>
              <tbody>
                {orgs.map((org) => (
                  <tr key={org.group_id}>
                    <td>{org.group_id}</td>
                    <td>{org.used}</td>
                    <td>{org.limit > 0 ? org.limit : t('instanceDetail.teamQuota.usage.orgUnlimited')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="card manager-page-table !p-0">
        {loading ? (
          <div className="p-4 text-sm text-muted">{t('common.loading')}</div>
        ) : error ? (
          <div className="p-4 text-sm text-danger">{t('errors.loadFailed', { detail: error })}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table w-max min-w-full">
              <thead>
                <tr>
                  <th />
                  <th>{t('instanceDetail.teamQuota.usage.userId')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.groupId')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.botId')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.used')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.limit')}</th>
                  <th>{t('instanceDetail.teamQuota.usage.default')}</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={7}>
                      <Empty text={t('instanceDetail.teamQuota.usage.empty')} />
                    </td>
                  </tr>
                ) : (
                  items.map((row) => {
                    const key = rowKey(row);
                    const expanded = Boolean(open[key]);
                    return (
                      <Fragment key={key}>
                        <tr>
                          <td>
                            {row.teams?.length ? (
                              <button
                                type="button"
                                className="btn sm ghost"
                                onClick={() => setOpen((cur) => ({ ...cur, [key]: !cur[key] }))}
                              >
                                {expanded ? '▾' : '▸'}
                              </button>
                            ) : null}
                          </td>
                          <td>{row.user_id}</td>
                          <td>{row.group_id || '—'}</td>
                          <td>{row.bot_id || '—'}</td>
                          <td>{row.used}</td>
                          <td>{row.limit}</td>
                          <td>{row.default}</td>
                        </tr>
                        {expanded
                          ? (row.teams ?? []).map((team) => (
                              <tr key={`${key}:${team.team_id}`} className="bg-secondary/20">
                                <td />
                                <td colSpan={3} className="text-xs text-muted">
                                  {t('instanceDetail.teamQuota.usage.teamId')}: {team.team_id}
                                </td>
                                <td>{team.used}</td>
                                <td>{team.limit}</td>
                                <td />
                              </tr>
                            ))
                          : null}
                      </Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
