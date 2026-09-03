import { expect, type Page } from '@playwright/test';
import { resolve } from 'node:path';

// cwd-relative (pnpm runs from frontend/), like the other lanes.
const OUT = resolve('../docs/site/assets/screenshots');

/** Viewport screenshot into docs/site/assets/screenshots/<name>.png (1440×900 @2x). */
export async function shot(page: Page, name: string): Promise<void> {
  await page.waitForLoadState('networkidle');
  // antd transitions settle in <300ms; give the last spinner a beat.
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/${name}.png`, animations: 'disabled' });
}

export async function heading(page: Page, name: string | RegExp, level = 3): Promise<void> {
  await expect(page.getByRole('heading', { name, level })).toBeVisible();
}
