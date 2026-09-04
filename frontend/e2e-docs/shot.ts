import { expect, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const OUT = fileURLToPath(new URL('../../docs/site/assets/screenshots/', import.meta.url));

/** Viewport screenshot into docs/site/assets/screenshots/<name>.png (1440×900 @2x). */
export async function shot(page: Page, name: string): Promise<void> {
  await page.waitForLoadState('networkidle');
  // antd transitions settle in <300ms; give the last spinner a beat.
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}${name}.png`, animations: 'disabled' });
}

export async function heading(page: Page, name: string | RegExp, level = 3): Promise<void> {
  await expect(page.getByRole('heading', { name, level })).toBeVisible();
}

/** Scroll the antd Card whose title contains `title` into view. */
export async function scrollCard(page: Page, title: string): Promise<void> {
  // The admin page scrolls an inner container, not the window — a raw DOM
  // `scrollIntoView()` inside `.evaluate()` silently no-ops there (#1864).
  // Playwright's own `scrollIntoViewIfNeeded()` drives the real scroll container,
  // but the page's cards load asynchronously and reflow as each arrives, so
  // scrolling before that settles targets a position that shifts out from under
  // it — wait for the network to go idle first.
  await page.waitForLoadState('networkidle');
  await page.getByText(title, { exact: true }).scrollIntoViewIfNeeded();
}
