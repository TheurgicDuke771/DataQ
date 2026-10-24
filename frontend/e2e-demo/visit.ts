import type { Page, Request } from '@playwright/test';

import { settle } from '../scripts/a11y/settle';

/** Hard cap on one route, tabs included. A healthy route takes 1-4 s; a route that hangs fails
 *  here, naming the route and what was still in flight, not at the test-wide ceiling. */
export const ROUTE_BUDGET_MS = 20_000;
/** How long the network may stay busy after a page or tab opens before it is treated as a
 *  background poll or a hung asset rather than data the route is still waiting on. Playwright's
 *  idle needs 500 ms of quiet, so this also covers a fetch issued a tick after the click. */
const QUIET_GRACE_MS = 1_500;
const PENDING_SHOWN = 8;

/** Long-lived by design, so never "pending": they would hold the grace open on every later step. */
const STREAMING = new Set(['eventsource', 'websocket']);

/** The requests started and not yet finished, per page, so a timeout can name them. */
const inFlight = new WeakMap<Page, Set<Request>>();

function track(page: Page): Set<Request> {
  const known = inFlight.get(page);
  if (known) return known;
  const open = new Set<Request>();
  const done = (request: Request) => void open.delete(request);
  page.on('request', (request) => {
    if (!STREAMING.has(request.resourceType())) open.add(request);
  });
  page.on('requestfinished', done);
  page.on('requestfailed', done);
  inFlight.set(page, open);
  return open;
}

/** Method and path only: a query string can carry a signed token, and CI logs are public. */
function describePending(page: Page): string {
  const open = [...track(page)];
  if (open.length === 0) return 'none';
  const shown = open.slice(0, PENDING_SHOWN).map((r) => {
    const url = new URL(r.url());
    return `${r.method()} ${url.pathname}`;
  });
  const more = open.length - shown.length;
  return shown.join(', ') + (more > 0 ? ` (+${more} more)` : '');
}

/** Open a route and every tab on it, so the requests behind each tab are made. `route` is
 *  app-relative (`/suites/…`); it resolves under the project's baseURL, sub-path included. */
export async function visit(page: Page, route: string): Promise<void> {
  const open = track(page);
  open.clear();
  const state = { over: false };
  let timer: NodeJS.Timeout | undefined;
  const budget = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      state.over = true;
      reject(
        new Error(
          `route ${route} exceeded its ${ROUTE_BUDGET_MS / 1000}s capture budget; requests still in flight: ${describePending(page)}`,
        ),
      );
    }, ROUTE_BUDGET_MS);
  });
  // The walk cannot be cancelled mid-await; it stops at its next step and whatever it throws
  // after the budget has already failed the route is dropped, not left unhandled.
  const walking = walk(page, route, state).catch((error: unknown) => {
    if (!state.over) throw error;
  });
  try {
    await Promise.race([walking, budget]);
  } finally {
    clearTimeout(timer);
  }
}

async function walk(page: Page, route: string, state: { over: boolean }): Promise<void> {
  await page.goto(route.replace(/^\//, ''));
  await idle(page, route);
  const tabs = page.getByRole('tab');
  for (let i = 0; i < (await tabs.count()); i += 1) {
    if (state.over) return;
    const tab = tabs.nth(i);
    if (!(await tab.isVisible()) || (await tab.getAttribute('aria-disabled')) === 'true') continue;
    await tab.click();
    await idle(page, route);
  }
}

/** Let the network go quiet for a short grace, then require the spinner gate (`settle`): a
 *  poll or hung asset must not hold the capture for the full 5 s a global idle wait used to
 *  take on every page and tab. Whatever is still open is logged, so a slow run is diagnosable
 *  from the CI output. */
async function idle(page: Page, route: string): Promise<void> {
  const quiet = await page
    .waitForLoadState('networkidle', { timeout: QUIET_GRACE_MS })
    .then(() => true)
    .catch(() => false);
  await settle(page);
  if (!quiet) {
    console.log(
      `[capture] ${route}: network busy past the grace; in flight: ${describePending(page)}`,
    );
  }
}
