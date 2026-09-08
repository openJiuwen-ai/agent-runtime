import { type RefObject, useEffect } from 'react';

/** Close a popover when a pointer starts outside the host element. */
export function useDismissWhenPointerLeaves(
  hostRef: RefObject<HTMLElement | null>,
  active: boolean,
  dismiss: () => void,
): void {
  useEffect(() => {
    if (!active) {
      return undefined;
    }

    const onPointerStart = (event: PointerEvent) => {
      const host = hostRef.current;
      if (!host) {
        return;
      }
      const path = event.composedPath();
      if (path.includes(host)) {
        return;
      }
      dismiss();
    };

    window.addEventListener('pointerdown', onPointerStart, true);
    return () => window.removeEventListener('pointerdown', onPointerStart, true);
  }, [active, dismiss, hostRef]);
}
