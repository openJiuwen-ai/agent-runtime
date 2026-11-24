import { useTranslation } from 'react-i18next';
import { useRouter } from '../../../router';
import { LogMaskingTab } from './LogMaskingTab';
import { ContentMaskingTab } from './ContentMaskingTab';
import { LoggingTab } from './LoggingTab';
import { AuditLogTab } from './AuditLogTab';
import { CronPolicyTab } from './CronPolicyTab';
import { OtherConfigTab } from './OtherConfigTab';

type ConfigTabKey = 'logMasking' | 'contentMasking' | 'logging' | 'auditLog' | 'cronPolicy' | 'other';

interface Props {
  instanceId: string;
}

export function InstanceConfigPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  const { path, params, navigate } = useRouter();
  const requestedTabRaw = params.get('configTab');
  // 旧链接 configTab=advanced → 其他配置
  const requestedTab = requestedTabRaw === 'advanced' ? 'other' : requestedTabRaw;
  const tab: ConfigTabKey = (
    ['contentMasking', 'logging', 'auditLog', 'cronPolicy', 'other'] as string[]
  ).includes(requestedTab || '')
    ? (requestedTab as ConfigTabKey)
    : 'logMasking';
  const setTab = (nextTab: ConfigTabKey) => {
    const query = new URLSearchParams(params);
    query.set('configTab', nextTab);
    navigate(`${path}?${query}`);
  };

  const tabs: { key: ConfigTabKey; label: string }[] = [
    { key: 'logMasking', label: t('instanceConfig.tabs.logMasking') },
    { key: 'contentMasking', label: t('instanceConfig.tabs.contentMasking') },
    { key: 'logging', label: t('instanceConfig.tabs.logging') },
    { key: 'auditLog', label: t('instanceConfig.tabs.auditLog') },
    { key: 'cronPolicy', label: t('instanceConfig.tabs.cronPolicy') },
    { key: 'other', label: t('instanceConfig.tabs.other') },
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
        {tab === 'contentMasking' && <ContentMaskingTab instanceId={instanceId} />}
        {tab === 'logging' && <LoggingTab instanceId={instanceId} />}
        {tab === 'auditLog' && <AuditLogTab instanceId={instanceId} />}
        {tab === 'cronPolicy' && <CronPolicyTab instanceId={instanceId} />}
        {tab === 'other' && <OtherConfigTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
