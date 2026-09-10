import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type HintTooltipProps = {
  text: string;
  className?: string;
  /** 可选跳转：配置后气泡内追加「前往配置 →」链接，点击执行 */
  linkTo?: () => void;
  /** 可选自定义气泡内容（如统一格式的 GuideLink）；配置后优先于 text/linkTo */
  children?: ReactNode;
};

const HIDE_DELAY_MS = 100;
const VIEWPORT_PAD = 8;

export function HintTooltip({ text, className, linkTo, children }: HintTooltipProps) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [open, setOpen] = useState(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);

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
    }, HIDE_DELAY_MS);
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
      Math.max(VIEWPORT_PAD, left),
      window.innerWidth - tipWidth - VIEWPORT_PAD,
    );
    el.style.left = `${left}px`;
    el.style.top = `${anchor.bottom + 6}px`;
  }, [open, anchor, text]);

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
        {children ? (
          children
        ) : linkTo ? (
          <button
            type="button"
            className="flex items-center gap-1 text-[11px] leading-snug text-muted no-underline hover:text-accent hover:underline cursor-pointer text-left m-0 p-0 bg-transparent border-0"
            onClick={() => {
              setOpen(false);
              setAnchor(null);
              linkTo();
            }}
          >
            <span>{text}</span>
            <span aria-hidden="true">→</span>
          </button>
        ) : (
          <p className="text-[11px] leading-snug text-muted m-0">{text}</p>
        )}
      </div>
    ) : null;

  return (
    <>
      <button
        type="button"
        className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[10px] font-medium leading-none text-muted hover:text-text hover:border-text-muted cursor-help ${className ?? ''}`}
        aria-label={text}
        onMouseEnter={(e) => show(e.currentTarget.getBoundingClientRect())}
        onMouseLeave={scheduleHide}
        onFocus={(e) => show(e.currentTarget.getBoundingClientRect())}
        onBlur={scheduleHide}
      >
        ?
      </button>
      {tooltip ? createPortal(tooltip, document.body) : null}
    </>
  );
}
