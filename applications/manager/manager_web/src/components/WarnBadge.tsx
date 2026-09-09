import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useRouter } from '../router';
import { requestGuideOpen, type GuideOpenTarget } from '../stores/guideStore';

export type WarnBadgeLink = {
  /** 提示文案（如「未配置 Agent定义」） */
  label: string;
  /** 点击后跳转的子路径 */
  to: string;
  /** 可选：跳转后由目标页面自动打开的添加弹框 */
  openTarget?: GuideOpenTarget;
};

type Props = {
  /** 悬停提示文案（无跳转时的纯文本） */
  text?: string;
  /** 可跳转的提示项：文字即链接，点击跳转对应页面 */
  links?: WarnBadgeLink[];
  className?: string;
};

/**
 * 引导告警徽标：红色圆圈 + 感叹号，悬停显示提示。
 * 传入 links 时，提示文字本身即链接（悬停变蓝下划线，点击跳转）。
 */
export function WarnBadge({ text, links, className }: Props) {
  const { navigate } = useRouter();
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [open, setOpen] = useState(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

  const hasLinks = !!links?.length;
  const ariaText = hasLinks ? links![0].label : text ?? '';

  const clearHideTimer = () => {
    if (hideTimerRef.current !== null) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  };

  const show = (rect: DOMRect) => {
    clearHideTimer();
    setAnchor(rect);
    setOpen(true);
  };

  const scheduleHide = () => {
    clearHideTimer();
    hideTimerRef.current = setTimeout(() => {
      setOpen(false);
      setAnchor(null);
      hideTimerRef.current = null;
    }, 100);
  };

  useEffect(() => () => clearHideTimer(), []);

  // 按实际宽度居中到图标正下方，并避免贴出视口
  useLayoutEffect(() => {
    if (!open || !anchor || !tooltipRef.current) return;
    const el = tooltipRef.current;
    const tipWidth = el.offsetWidth;
    const centerX = anchor.left + anchor.width / 2;
    let left = centerX - tipWidth / 2;
    left = Math.min(
      Math.max(8, left),
      window.innerWidth - tipWidth - 8,
    );
    el.style.left = `${left}px`;
    el.style.top = `${anchor.bottom + 6}px`;
  }, [open, anchor]);

  const tooltip =
    open && anchor ? (
      <div
        ref={tooltipRef}
        className="fixed z-[200] max-w-[18rem] rounded-md border border-[var(--border)] bg-[var(--card)] px-2.5 py-2 shadow-lg"
        style={{ left: 0, top: anchor.bottom + 6 }}
        role="tooltip"
        onMouseEnter={clearHideTimer}
        onMouseLeave={scheduleHide}
      >
        {hasLinks ? (
          <div className="flex flex-col gap-1">
            {links!.map((link) => (
              <button
                key={link.to + link.label}
                type="button"
                className="flex items-center gap-1 text-[11px] leading-snug text-danger no-underline hover:text-accent hover:underline cursor-pointer text-left m-0 p-0 bg-transparent border-0"
                onClick={(e) => {
                  // 徽标嵌在页签/导航按钮内，阻止冒泡避免父级把跳转覆盖回默认页签
                  e.stopPropagation();
                  setOpen(false);
                  setAnchor(null);
                  if (link.openTarget) requestGuideOpen(link.openTarget);
                  navigate(link.to);
                }}
              >
                <span>{link.label}</span>
                <span aria-hidden="true">→</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[11px] leading-snug text-danger m-0">{text}</p>
        )}
      </div>
    ) : null;

  return (
    <>
      <span
        className={`inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-danger text-danger text-[9px] font-bold leading-none opacity-80 cursor-help ${className ?? ''}`}
        aria-label={ariaText}
        onMouseEnter={(e) => show(e.currentTarget.getBoundingClientRect())}
        onMouseLeave={scheduleHide}
      >
        !
      </span>
      {tooltip ? createPortal(tooltip, document.body) : null}
    </>
  );
}
