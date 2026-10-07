import type { Page } from '@playwright/test';

import { settle } from '../scripts/a11y/settle';

/** Open a route and every tab on it, so the requests behind each tab are made. `route` is
 *  app-relative (`/suites/…`); it resolves under the project's baseURL, sub-path included. */
export async function visit(page: Page, route: string): Promise<void> {
  await page.goto(route.replace(/^\//, ''));
  await idle(page);
  const tabs = page.getByRole('tab');
  for (let i = 0; i < (await tabs.count()); i += 1) {
    const tab = tabs.nth(i);
    if (!(await tab.isVisible()) || (await tab.getAttribute('aria-disabled')) === 'true') continue;
    await tab.click();
    await idle(page);
  }
}

async function idle(page: Page): Promise<void> {
  // A polling page never goes network-idle for long; the spinner check below is the real gate.
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
  await settle(page);
}
