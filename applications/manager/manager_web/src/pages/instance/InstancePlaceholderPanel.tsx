import { useTranslation } from 'react-i18next';
import { Empty } from '../../components/Empty';

/** 实例详情下暂未实现功能的占位面板。 */
export function InstancePlaceholderPanel({
  titleKey,
  subtitleKey,
  emptyKey = 'instanceDetail.placeholder.empty',
}: {
  titleKey: string;
  subtitleKey: string;
  emptyKey?: string;
}) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="page-title text-base">{t(titleKey)}</div>
        <div className="page-subtitle">{t(subtitleKey)}</div>
      </div>
      <div className="card">
        <Empty text={t(emptyKey)} />
      </div>
    </div>
  );
}
