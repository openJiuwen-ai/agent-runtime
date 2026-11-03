import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { WorkspaceQuotaPolicyTab } from './WorkspaceQuotaPolicyTab';
import { WorkspaceQuotaUsageTab } from './WorkspaceQuotaUsageTab';
import './QuotaSections.css';

interface Props {
  instanceId: string;
}

export function WorkspaceQuotaPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [usageRevision, setUsageRevision] = useState(0);
  const refreshUsage = useCallback(() => setUsageRevision(current => current + 1), []);

  return (
    <div className="quota-sections">
      <section className="quota-section" aria-label={t('instanceDetail.workspaceQuota.tabs.policy')}>
        <WorkspaceQuotaPolicyTab instanceId={instanceId} onChange={refreshUsage} />
      </section>
      <section className="quota-section" aria-label={t('instanceDetail.workspaceQuota.tabs.usage')}>
        <WorkspaceQuotaUsageTab instanceId={instanceId} refreshKey={usageRevision} />
      </section>
    </div>
  );
}
