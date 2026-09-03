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
