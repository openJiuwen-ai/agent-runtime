import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LogMaskingTab } from './LogMaskingTab';
import { LoggingTab } from './LoggingTab';

type ConfigTabKey = 'logMasking' | 'logging';

interface Props {
  instanceId: string;
}

export function InstanceConfigPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<ConfigTabKey>('logMasking');

  const tabs: { key: ConfigTabKey; label: string }[] = [
    { key: 'logMasking', label: t('instanceConfig.tabs.logMasking') },
    { key: 'logging', label: t('instanceConfig.tabs.logging') },
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
        {tab === 'logMasking' && <LogMaskingTab instanceId={instanceId} />}
        {tab === 'logging' && <LoggingTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
