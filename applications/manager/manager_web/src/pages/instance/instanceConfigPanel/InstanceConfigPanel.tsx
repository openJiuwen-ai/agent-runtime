import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LogMaskingTab } from './LogMaskingTab';
import { LoggingTab } from './LoggingTab';
import { AuditLogTab } from './AuditLogTab';
import { CronPolicyTab } from './CronPolicyTab';
import { AdvancedConfigTab } from './AdvancedConfigTab';

type ConfigTabKey = 'logMasking' | 'logging' | 'auditLog' | 'cronPolicy' | 'advanced';

interface Props {
  instanceId: string;
}

export function InstanceConfigPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<ConfigTabKey>('logMasking');

  const tabs: { key: ConfigTabKey; label: string }[] = [
    { key: 'logMasking', label: t('instanceConfig.tabs.logMasking') },
    { key: 'logging', label: t('instanceConfig.tabs.logging') },
    { key: 'auditLog', label: t('instanceConfig.tabs.auditLog') },
    { key: 'cronPolicy', label: t('instanceConfig.tabs.cronPolicy') },
    { key: 'advanced', label: t('instanceConfig.tabs.advanced') },
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
        {tab === 'auditLog' && <AuditLogTab instanceId={instanceId} />}
        {tab === 'cronPolicy' && <CronPolicyTab instanceId={instanceId} />}
        {tab === 'advanced' && <AdvancedConfigTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
