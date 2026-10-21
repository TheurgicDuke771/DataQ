import { useCallback, useRef } from 'react';

/** Where focus goes when the control that had it is gone (a dialog's trigger removed by the
 *  save it made, a button disabled while it saves): the element `ref` is put on, which needs
 *  `tabIndex={-1}`. `restore()` waits a frame, so a dialog's own focus return runs first. */
export function useFocusFallback<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T>(null);
  const restore = useCallback(() => {
    requestAnimationFrame(() => {
      const active = document.activeElement;
      if (!active || active === document.body || !active.isConnected) ref.current?.focus();
    });
  }, []);
  return { ref, restore };
}
