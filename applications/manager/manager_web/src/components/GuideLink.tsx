import { useTranslation } from 'react-i18next';
import { useRouter } from '../router';

/**
 * 统一格式引导链接：「前往 {页面} →」。
 * 所有"跳转到对应页面"的友好提示链接都应使用本组件，保证文案与样式一致。
 */
export function GuideLink({
  page,
  to,
  className = 'text-[11px] text-accent hover:text-accent-hover hover:underline',
  onNavigate,
}: {
  /** 页面名称（如「用户管理」「模型」），直接拼入「前往 {page} 页面」文案 */
  page: string;
  /** 点击后跳转的子路径 */
  to: string;
  className?: string;
  /** 跳转前的附加动作（如关闭弹窗、记录待打开的添加弹框） */
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  return (
    <button
      type="button"
      className={`flex items-center gap-1 cursor-pointer bg-transparent border-0 p-0 m-0 no-underline text-left ${className}`}
      onClick={(e) => {
        // 链接常嵌在页签/导航按钮内，阻止冒泡避免父级把跳转覆盖回默认行为
        e.stopPropagation();
        onNavigate?.();
        navigate(to);
      }}
    >
      <span>{t('guide.gotoPageLink', { page })}</span>
      <span aria-hidden="true">→</span>
    </button>
  );
}

/**
 * 统一格式引导告警文案：「未配置 {xxx}」。
 * 所有红色叹号（WarnBadge）的提示项都应使用本函数生成文案，保证格式一致。
 */
export function useGuideMissingLabel() {
  const { t } = useTranslation();
  return (item: string) => t('guide.missingItem', { item });
}
