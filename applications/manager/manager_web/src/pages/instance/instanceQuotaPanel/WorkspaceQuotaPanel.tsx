import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { WorkspaceQuotaPolicyTab } from './WorkspaceQuotaPolicyTab';
import { WorkspaceQuotaUsageTab } from './WorkspaceQuotaUsageTab';

type WorkspaceQuotaTabKey = 'policy' | 'usage';

interface Props {
  instanceId: string;
}

/** 用户空间配额：策略管理 + 用量统计。 */
export function WorkspaceQuotaPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<WorkspaceQuotaTabKey>('policy');

  const tabs: { key: WorkspaceQuotaTabKey; label: string }[] = [
    { key: 'policy', label: t('instanceDetail.workspaceQuota.tabs.policy') },
    { key: 'usage', label: t('instanceDetail.workspaceQuota.tabs.usage') },
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
        {tab === 'policy' && <WorkspaceQuotaPolicyTab instanceId={instanceId} />}
        {tab === 'usage' && <WorkspaceQuotaUsageTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
