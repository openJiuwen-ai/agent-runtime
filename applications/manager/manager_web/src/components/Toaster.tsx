import { useUiStore, type Toast } from '../stores/uiStore';

const kindClass: Record<Toast['kind'], string> = {
  success: 'toast-item--success',
  warn: 'toast-item--warn',
  danger: 'toast-item--danger',
  info: 'toast-item--info',
};

export function Toaster() {
  const toasts = useUiStore((s) => s.toasts);
  const leavingIds = useUiStore((s) => s.leavingIds);
  const dismissToast = useUiStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-32 right-0 z-[80] flex w-[min(18rem,calc(100vw-2rem))] flex-col items-end gap-2 px-4 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          onClick={() => dismissToast(t.id)}
          className={`toast-item toast-item--slide cursor-pointer pointer-events-auto w-full rounded-lg px-4 py-1.5 text-sm leading-relaxed whitespace-normal break-words [overflow-wrap:anywhere] text-left ${
            kindClass[t.kind]
          } ${leavingIds.has(t.id) ? 'leaving' : ''}`}
          role="status"
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
