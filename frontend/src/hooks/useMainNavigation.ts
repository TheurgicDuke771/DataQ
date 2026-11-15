import { type RefObject, useCallback, useEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

// How long a Back/Forward restore keeps retrying while the page it returns to is still loading
// its rows (a restore onto a page shorter than the offset is clamped by the browser).
const RESTORE_FRAMES = 60;

/**
 * What a page change does to the app's one scroll container, `<main>`: focus and scroll.
 *
 * - A new page takes focus, so a screen reader announces it and Tab starts from the page, and it
 *   opens at the top instead of at the scroll offset of the page before it.
 * - Back and Forward return to the offset the entry was left at. The browser restores only the
 *   window's scroll, and the window never scrolls here, so the app records each entry's offset.
 * - A navigation that leaves focus on a live control inside the page (an admin tab, a suite in the
 *   master list) is not a new page: the user's focus and place are both kept.
 *
 * Returns the `onScroll` handler for `<main>`, which records the offsets.
 */
export function useMainNavigation(mainRef: RefObject<HTMLElement | null>): () => void {
  const location = useLocation();
  const navigationType = useNavigationType();
  const lastPath = useRef(location.pathname);
  const entry = useRef(location.key);
  const offsets = useRef(new Map<string, number>());

  useEffect(() => {
    entry.current = location.key;
    // Compared against the last path rather than "skip the first run": StrictMode runs effects
    // twice. A change of query string alone is the same page.
    if (lastPath.current === location.pathname) return;
    lastPath.current = location.pathname;
    const main = mainRef.current;
    if (!main) return;
    // <main> itself holding focus is where the last page change left it, not a control.
    const active = document.activeElement;
    if (active !== main && main.contains(active)) return;
    main.focus({ preventScroll: true });
    const target = navigationType === 'POP' ? (offsets.current.get(location.key) ?? 0) : 0;
    main.scrollTop = target;
    if (main.scrollTop >= target) return;
    // The returned-to page is still loading: keep restoring until it is tall enough, unless the
    // user scrolls first.
    let frames = 0;
    let set = main.scrollTop;
    let frame = requestAnimationFrame(function retry() {
      if (main.scrollTop !== set || frames++ >= RESTORE_FRAMES) return;
      main.scrollTop = target;
      set = main.scrollTop;
      if (set < target) frame = requestAnimationFrame(retry);
    });
    return () => cancelAnimationFrame(frame);
  }, [location.pathname, location.key, navigationType, mainRef]);

  return useCallback(() => {
    if (mainRef.current) offsets.current.set(entry.current, mainRef.current.scrollTop);
  }, [mainRef]);
}
