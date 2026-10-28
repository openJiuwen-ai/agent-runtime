import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TeamQuotaPolicyTab } from './TeamQuotaPolicyTab';
import { TeamQuotaUsageTab } from './TeamQuotaUsageTab';

type TeamQuotaTabKey = 'policy' | 'usage';

interface Props {
  instanceId: string;
}

export function TeamQuotaPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TeamQuotaTabKey>('policy');

  const tabs: { key: TeamQuotaTabKey; label: string }[] = [
    { key: 'policy', label: t('instanceDetail.teamQuota.tabs.policy') },
    { key: 'usage', label: t('instanceDetail.teamQuota.tabs.usage') },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="tabs-bar">
        {tabs.map((it) => (
          <button
            key={it.key}
            type="button"
            onClick={() => setTab(it.key)}
            className={`tab ${tab === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>
      <div>
        {tab === 'policy' && <TeamQuotaPolicyTab instanceId={instanceId} />}
        {tab === 'usage' && <TeamQuotaUsageTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
