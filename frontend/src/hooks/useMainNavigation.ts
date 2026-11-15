import { type RefObject, useCallback, useLayoutEffect, useMemo, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

// How long a Back/Forward restore keeps retrying while the page it returns to is still loading
// its rows (a restore onto a page shorter than the offset is clamped by the browser).
const RESTORE_MS = 3000;
// Offsets are kept per history entry for the tab, and saved to sessionStorage on each page change
// and on pagehide, so a reload keeps them (the router's entry keys live in history.state and
// survive it too). Oldest dropped first.
const STORE_KEY = 'dq-main-scroll';
const MAX_ENTRIES = 100;
// Input that means the user is scrolling: a restore in progress stops for it.
const USER_SCROLL_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

function loadOffsets(): Map<string, number> {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    const pairs: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(pairs)) {
      return new Map(
        pairs.filter(
          (p): p is [string, number] =>
            Array.isArray(p) && typeof p[0] === 'string' && typeof p[1] === 'number',
        ),
      );
    }
  } catch {
    // Storage unavailable or corrupt: start empty.
  }
  return new Map();
}

function saveOffsets(offsets: Map<string, number>): void {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify([...offsets]));
  } catch {
    // Storage full or unavailable: offsets are kept in memory only.
  }
}

/**
 * What a page change does to the app's one scroll container, `<main>`: focus and scroll.
 *
 * - A new page takes focus, so a screen reader announces it and Tab starts from the page, and it
 *   opens at the top instead of at the scroll offset of the page before it.
 * - Back and Forward return to the offset the entry was left at. The browser restores only the
 *   window's scroll, and the window never scrolls here, so the app records each entry's offset.
 * - A navigation made from a control inside the page that is still there afterwards (an admin
 *   tab, a suite in the master list) is not a new page: the user's focus and place are both kept.
 *
 * Returns the `onScroll` handler for `<main>`, which records the offsets.
 */
export function useMainNavigation(mainRef: RefObject<HTMLElement | null>): () => void {
  const location = useLocation();
  const navigationType = useNavigationType();
  const lastPath = useRef(location.pathname);
  const entry = useRef(location.key);
  const offsets = useRef<Map<string, number> | null>(null);
  offsets.current ??= loadOffsets();
  const restore = useRef<{ stop: () => void } | null>(null);
  // Focus as it was before this navigation's page mounted: render runs before the commit, so a
  // new page focusing something of its own on mount (autoFocus) is not mistaken for the user's
  // control. Read once per path, in the render that first sees it.
  const focusBefore = useMemo(
    () => document.activeElement,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read anew on each page change
    [location.pathname],
  );

  // A layout effect: the entry key and the reset land before paint, so a scroll event the new
  // page's height causes is never recorded against the page before it.
  useLayoutEffect(() => {
    entry.current = location.key;
    // Compared against the last path rather than "skip the first run": StrictMode runs effects
    // twice. A change of query string alone is the same page, and leaves a restore running.
    if (lastPath.current === location.pathname) return;
    lastPath.current = location.pathname;
    restore.current?.stop();
    if (offsets.current) saveOffsets(offsets.current);
    const main = mainRef.current;
    if (!main) return;
    const before = focusBefore;
    // <main> itself holding focus is where the last page change left it, not a control.
    if (before && before !== main && main.contains(before) && document.activeElement === before) {
      return;
    }
    if (!main.contains(document.activeElement) || document.activeElement === main) {
      main.focus({ preventScroll: true });
    }
    const target = navigationType === 'POP' ? (offsets.current?.get(location.key) ?? 0) : 0;
    main.scrollTop = target;
    if (main.scrollTop >= target) return;
    // The returned-to page is still loading: keep restoring until it is tall enough, for a
    // while, unless the user scrolls. Offsets are not recorded meanwhile, so a clamped
    // position never overwrites the one being restored.
    const until = performance.now() + RESTORE_MS;
    let frame = 0;
    let stopped = false;
    const stop = () => {
      stopped = true;
      cancelAnimationFrame(frame);
      USER_SCROLL_EVENTS.forEach((e) => main.removeEventListener(e, stop));
      if (restore.current?.stop === stop) restore.current = null;
    };
    const retry = () => {
      if (stopped) return;
      main.scrollTop = target;
      if (main.scrollTop >= target || performance.now() >= until) stop();
      else frame = requestAnimationFrame(retry);
    };
    USER_SCROLL_EVENTS.forEach((e) => main.addEventListener(e, stop, { passive: true }));
    restore.current = { stop };
    frame = requestAnimationFrame(retry);
  }, [location.pathname, location.key, navigationType, mainRef, focusBefore]);

  // A reload or tab close saves the offsets; unmount stops any restore still running.
  useLayoutEffect(() => {
    const save = () => offsets.current && saveOffsets(offsets.current);
    window.addEventListener('pagehide', save);
    return () => {
      window.removeEventListener('pagehide', save);
      restore.current?.stop();
    };
  }, []);

  return useCallback(() => {
    const main = mainRef.current;
    const store = offsets.current;
    if (!main || !store || restore.current) return;
    store.delete(entry.current);
    store.set(entry.current, main.scrollTop);
    while (store.size > MAX_ENTRIES) store.delete(store.keys().next().value as string);
  }, [mainRef]);
}
