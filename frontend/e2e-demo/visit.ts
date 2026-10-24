import type { Page, Request } from '@playwright/test';

import { settle } from '../scripts/a11y/settle';

/** Hard cap on one route, tabs included. A healthy route takes 1-4 s; a route that hangs fails
 *  here, naming the route and what was still in flight, not at the test-wide ceiling. */
export const ROUTE_BUDGET_MS = 30_000;
/** How long a request may stay open after the page has settled before it is treated as a
 *  background poll or a hung asset rather than data the route is still waiting on. */
const QUIET_GRACE_MS = 1_500;

/** The requests started and not yet finished, per page, so a timeout can name them. */
const inFlight = new WeakMap<Page, Set<Request>>();

function track(page: Page): Set<Request> {
  const known = inFlight.get(page);
  if (known) return known;
  const open = new Set<Request>();
  const done = (request: Request) => void open.delete(request);
  page.on('request', (request) => open.add(request));
  page.on('requestfinished', done);
  page.on('requestfailed', done);
  inFlight.set(page, open);
  return open;
}

function describePending(page: Page): string {
  const open = [...track(page)];
  return open.length === 0 ? 'none' : open.map((r) => `${r.method()} ${r.url()}`).join(', ');
}

/** Open a route and every tab on it, so the requests behind each tab are made. `route` is
 *  app-relative (`/suites/…`); it resolves under the project's baseURL, sub-path included. */
export async function visit(page: Page, route: string): Promise<void> {
  track(page);
  let timer: NodeJS.Timeout | undefined;
  const budget = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new Error(
            `route ${route} exceeded its ${ROUTE_BUDGET_MS / 1000}s capture budget; requests still in flight: ${describePending(page)}`,
          ),
        ),
      ROUTE_BUDGET_MS,
    );
  });
  try {
    await Promise.race([walk(page, route), budget]);
  } finally {
    clearTimeout(timer);
  }
}

async function walk(page: Page, route: string): Promise<void> {
  await page.goto(route.replace(/^\//, ''));
  await idle(page, route);
  const tabs = page.getByRole('tab');
  for (let i = 0; i < (await tabs.count()); i += 1) {
    const tab = tabs.nth(i);
    if (!(await tab.isVisible()) || (await tab.getAttribute('aria-disabled')) === 'true') continue;
    await tab.click();
    await idle(page, route);
  }
}

/** The spinner gate (`settle`) says the data is on screen. A request still open after it is a
 *  poll or a hung asset: give ordinary stragglers a short grace, then move on and log what
 *  is left, so a slow run is diagnosable from the CI output. */
async function idle(page: Page, route: string): Promise<void> {
  await settle(page);
  const open = track(page);
  const deadline = Date.now() + QUIET_GRACE_MS;
  while (open.size > 0 && Date.now() < deadline) await page.waitForTimeout(50);
  if (open.size > 0) {
    console.log(`[capture] ${route}: not idle after settle; in flight: ${describePending(page)}`);
  }
}
