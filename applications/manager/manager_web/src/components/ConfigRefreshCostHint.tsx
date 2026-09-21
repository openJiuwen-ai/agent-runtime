import { useTranslation } from 'react-i18next';

/**
 * 配置写操作的运行时代价提示：经网关下发的配置变更会触发 agent-runtime
 * config_refresh —— 运行时下全部 AgentServer Pod 优雅日落并按新配置重建。
 *
 * - scope="direct"：变更必然下发（实例资源授予 / 日志配置等），直接陈述代价
 * - scope="template"：模板编辑仅在已被实例引用时下发，文案带“若已被引用”限定
 */
export function ConfigRefreshCostHint({
  scope = 'direct',
}: {
  scope?: 'direct' | 'template';
}) {
  const { t } = useTranslation();
  return (
    <div className="cost-hint" role="note">
      <svg
        className="cost-hint__icon"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        strokeWidth={2}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"
        />
      </svg>
      <span>
        {scope === 'template'
          ? t('common.configRefreshCostTemplate')
          : t('common.configRefreshCost')}
      </span>
    </div>
  );
}
