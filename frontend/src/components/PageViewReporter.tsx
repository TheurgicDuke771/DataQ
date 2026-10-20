import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

import { reportEvent, routeName } from '../api/telemetry';

/** Reports a `page_viewed` usage event per route change — the route NAME, never the URL.
 *  Rendered inside the auth gate, so it never asks before sign-in. */
export function PageViewReporter() {
  const { pathname } = useLocation();
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (last.current === pathname) return;
    last.current = pathname;
    reportEvent({ name: 'page_viewed', attributes: { route: routeName(pathname) } });
  }, [pathname]);
  return null;
}
