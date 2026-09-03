import { useCallback, useEffect, useRef } from 'react';

/**
 * 跟踪表单相对「干净快照」是否有未保存修改。
 * 在弹窗打开并完成表单初始化后调用 markClean(初始值)。
 */
export function useFormDirty(open: boolean) {
  const baselineRef = useRef('');

  useEffect(() => {
    if (!open) baselineRef.current = '';
  }, [open]);

  const markClean = useCallback((value: unknown) => {
    baselineRef.current = JSON.stringify(value);
  }, []);

  const isDirty = useCallback(
    (value: unknown) => {
      if (!open || !baselineRef.current) return false;
      return JSON.stringify(value) !== baselineRef.current;
    },
    [open],
  );

  return { markClean, isDirty };
}
