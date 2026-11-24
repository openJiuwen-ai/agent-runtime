import { InstanceUsersTab } from './InstanceUsersTab';
import { InstanceOrgsTab } from './InstanceOrgsTab';
import { InstanceAdmissionSections } from './InstanceAdmissionSections';

/** 实例准入：组织、用户独立管理，两个区块始终可见。 */
export function InstanceAccessPanel({ instanceId }: { instanceId: string }) {
  return (
    <InstanceAdmissionSections
      key={instanceId}
      organizations={<InstanceOrgsTab instanceId={instanceId} />}
      users={<InstanceUsersTab instanceId={instanceId} />}
    />
  );
}
