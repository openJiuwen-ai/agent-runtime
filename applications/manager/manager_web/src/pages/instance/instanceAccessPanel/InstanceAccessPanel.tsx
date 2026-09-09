import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { InstanceUsersTab } from './InstanceUsersTab';
import { InstanceOrgsTab } from './InstanceOrgsTab';
import { useGuideStore } from '../../../stores/guideStore';

type AccessTabKey = 'users' | 'orgs';

interface Props {
  instanceId: string;
}

/** 实例准入：谁能进入本实例（instance_grant）。 */
export function InstanceAccessPanel({ instanceId }: Props) {
  const { t } = useTranslation();
  /** 引导跳转「未配置准入组织」时直接落在组织页签 */
  const openTarget = useGuideStore((s) => s.openTarget);
  const [tab, setTab] = useState<AccessTabKey>(openTarget === 'accessOrgs' ? 'orgs' : 'users');

  useEffect(() => {
    if (openTarget === 'accessOrgs') setTab('orgs');
  }, [openTarget]);

  const tabs: { key: AccessTabKey; label: string }[] = [
    { key: 'users', label: t('instanceDetail.accessPanel.tabs.users') },
    { key: 'orgs', label: t('instanceDetail.accessPanel.tabs.orgs') },
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
        {tab === 'users' && <InstanceUsersTab instanceId={instanceId} />}
        {tab === 'orgs' && <InstanceOrgsTab instanceId={instanceId} />}
      </div>
    </div>
  );
}
