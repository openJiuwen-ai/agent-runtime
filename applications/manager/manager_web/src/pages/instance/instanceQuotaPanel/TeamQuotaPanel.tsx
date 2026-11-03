import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TeamQuotaPolicyTab } from './TeamQuotaPolicyTab';
import { TeamQuotaUsageTab } from './TeamQuotaUsageTab';
import './QuotaSections.css';

interface Props {
  instanceId: string;
}

export function TeamQuotaPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [usageRevision, setUsageRevision] = useState(0);
  const refreshUsage = useCallback(() => setUsageRevision(current => current + 1), []);

  return (
    <div className="quota-sections">
      <section className="quota-section" aria-label={t('instanceDetail.teamQuota.tabs.policy')}>
        <TeamQuotaPolicyTab instanceId={instanceId} onChange={refreshUsage} />
      </section>
      <section className="quota-section" aria-label={t('instanceDetail.teamQuota.tabs.usage')}>
        <TeamQuotaUsageTab instanceId={instanceId} refreshKey={usageRevision} />
      </section>
    </div>
  );
}
