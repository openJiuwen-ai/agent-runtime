import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceAgentResourceTab } from './InstanceAgentResourceTab';
import { InstanceServiceResourceTab } from './InstanceServiceResourceTab';

type ResourceTabKey = 'agent' | 'serviceResource';

interface Props {
  instanceId: string;
}

export function InstanceResourcePanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<ResourceTabKey>('agent');

  const tabs: { key: ResourceTabKey; label: string }[] = [
    { key: 'agent', label: t('instanceDetail.resourcePanel.tabs.agent') },
    { key: 'serviceResource', label: t('instanceDetail.resourcePanel.tabs.serviceResource') },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="tabs-bar">
        {tabs.map((it) => (
          <button
            key={it.key}
            onClick={() => setTab(it.key)}
            className={`tab ${tab === it.key ? 'active' : ''}`}
          >
            {it.label}
          </button>
        ))}
      </div>

      <div>
        {tab === 'agent' && <InstanceAgentResourceTab instanceId={instanceId} />}
        {tab === 'serviceResource' && <InstanceServiceResourceTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
