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
  /** 引导跳转「未配置准入用户」时直接落在用户页签（组织页签排在第一） */
  const openTarget = useGuideStore((s) => s.openTarget);
  const [tab, setTab] = useState<AccessTabKey>(openTarget === 'accessUsers' ? 'users' : 'orgs');

  useEffect(() => {
    if (openTarget === 'accessUsers') setTab('users');
  }, [openTarget]);

  /** 页签顺序：组织在前（更重要），用户在后 */
  const tabs: { key: AccessTabKey; label: string }[] = [
    { key: 'orgs', label: t('instanceDetail.accessPanel.tabs.orgs') },
    { key: 'users', label: t('instanceDetail.accessPanel.tabs.users') },
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
