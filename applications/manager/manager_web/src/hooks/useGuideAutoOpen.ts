import { useEffect } from 'react';
import { useGuideStore, type GuideOpenTarget } from '../stores/guideStore';

/**
 * 引导跳转：其他页面通过 requestOpen(target) 记录「要打开的添加弹框」并跳转到本页面，
 * 本页面挂载后检测到标记即打开弹框并清除标记。
 */
export function useGuideAutoOpen(target: GuideOpenTarget, open: () => void) {
  const openTarget = useGuideStore((s) => s.openTarget);
  const consumeOpen = useGuideStore((s) => s.consumeOpen);

  useEffect(() => {
    if (openTarget !== target) return;
    consumeOpen();
    open();
    // open 由调用方保证稳定（通常是 setState 或 setLoading 类回调）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openTarget, target, consumeOpen]);
}
