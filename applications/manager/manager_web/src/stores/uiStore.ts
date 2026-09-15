import { create } from 'zustand';

export type Toast = {
  id: string;
  kind: 'info' | 'success' | 'warn' | 'danger';
  message: string;
};

/** 离场动画时长，与 index.css 的 .toast-item--slide.leaving 保持一致 */
const LEAVE_DURATION_MS = 300;

interface UiState {
  toasts: Toast[];
  /** 已进入离场动画、即将移除的 toast id */
  leavingIds: Set<string>;
  pushToast: (t: Omit<Toast, 'id'>) => void;
  /** 先播放滑出动画，动画结束后才真正移除 */
  dismissToast: (id: string) => void;
}

export const useUiStore = create<UiState>((set) => ({
  toasts: [],
  leavingIds: new Set<string>(),
  pushToast: (t) =>
    set((s) => {
      const id = `t-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
      const toast: Toast = { id, ...t };
      setTimeout(() => {
        useUiStore.getState().dismissToast(id);
      }, 4500);
      return { toasts: [...s.toasts, toast] };
    }),
  dismissToast: (id) =>
    set((s) => {
      // 已在离场中则不重复触发
      if (s.leavingIds.has(id)) return s;
      const leavingIds = new Set(s.leavingIds);
      leavingIds.add(id);
      setTimeout(() => {
        useUiStore.setState((cur) => ({
          toasts: cur.toasts.filter((x) => x.id !== id),
          leavingIds: (() => {
            const next = new Set(cur.leavingIds);
            next.delete(id);
            return next;
          })(),
        }));
      }, LEAVE_DURATION_MS);
      return { leavingIds };
    }),
}));

export function toast(kind: Toast['kind'], message: string) {
  useUiStore.getState().pushToast({ kind, message });
}
