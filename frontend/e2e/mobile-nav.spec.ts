import { expect, test } from '@playwright/test';

// Mobile overlay nav (#801). On a narrow (≤ lg) viewport the Sider collapses to
// zero width and the nav moves into an overlay Drawer that floats *above* the
// content with a scrim — it must never squeeze the page. Desktop behaviour is
// covered by smoke.spec.ts; this pins the mobile layout at 390px (iPhone-class).
test.describe('mobile overlay nav (390px)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('nav overlays the content instead of consuming layout width', async ({ page }) => {
    await page.goto('/dashboard');

    // The inline nav is gone at this width — no visible sider nav link, and the
    // ☰ toggle is the way in.
    const toggle = page.getByRole('button', { name: 'Toggle navigation' });
    await expect(toggle).toBeVisible();
    await expect(page.getByRole('link', { name: 'Assets' })).toHaveCount(0);

    // The Sider has collapsed to zero width; wait out its CSS width transition so
    // the content has grown to the full viewport (390px, minus a hairline border).
    // The invariant #801 is fixing: the nav must not eat content width.
    await expect(page.locator('.ant-layout-sider')).toHaveClass(/ant-layout-sider-zero-width/);
    const content = page.locator('.ant-layout-content');
    const fullWidth = async () => (await content.boundingBox())?.width ?? 0;
    await expect.poll(fullWidth).toBeGreaterThanOrEqual(388);

    // Open the overlay: a dialog (the Drawer) with its scrim appears above the page.
    await toggle.click();
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();
    await expect(page.locator('.ant-drawer-mask')).toBeVisible();
    await expect(drawer.getByRole('link', { name: 'Assets' })).toBeVisible();

    // The content keeps its full width with the nav open — the Drawer overlays it,
    // it does not reflow the page (the whole point of #801).
    await expect.poll(fullWidth).toBeGreaterThanOrEqual(388);

    // Choosing a nav item navigates and closes the overlay.
    await drawer.getByRole('link', { name: 'Assets' }).click();
    await expect(page).toHaveURL(/\/assets$/);
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('tapping the scrim closes the overlay without navigating', async ({ page }) => {
    await page.goto('/dashboard');
    await page.getByRole('button', { name: 'Toggle navigation' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();

    await page.locator('.ant-drawer-mask').click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});
