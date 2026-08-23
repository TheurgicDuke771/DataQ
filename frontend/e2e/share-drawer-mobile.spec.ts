import { expect, test } from '@playwright/test';

// Share drawer on a phone (#829).
test.describe('share drawer (390px)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the whole Add button is on-screen and the drawer does not overflow', async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);

    await page.getByRole('button', { name: 'Share', exact: true }).click();
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();

    // It is an overlay: above the page behind a scrim, panel clamped to the viewport.
    await expect(page.locator('.ant-drawer-mask')).toBeVisible();
    const panel = page.locator('.ant-drawer-content-wrapper');
    await expect
      .poll(async () => {
        const box = await panel.boundingBox();
        return box ? Math.round(box.x + box.width) : 0;
      })
      .toBeLessThanOrEqual(390);

    // Trap 1: don't measure until the row exists (AsyncBody has resolved).
    const add = drawer.getByRole('button', { name: 'Add', exact: true });
    await expect(add).toBeVisible();

    // Trap 2: the whole button, not merely a sliver of it. This is THE assertion
    // that fails if SharePanel's fix is reverted.
    await expect(add).toBeInViewport({ ratio: 1 });

    // And the row reflowed rather than overflowing. Polled, because it is measured
    // across the drawer's slide-in transition.
    await expect
      .poll(async () =>
        page.locator('.ant-drawer-body').evaluate((el) => el.scrollWidth - el.clientWidth),
      )
      .toBeLessThanOrEqual(0);

    // Both pickers a user needs to actually grant access survive the reflow — wrapping must not
    // push the permission picker out to make room for the button.
    const addRow = add.locator('xpath=..');
    const pickers = addRow.locator('.ant-select');
    await expect(pickers).toHaveCount(2);
    await expect(pickers.first()).toBeInViewport({ ratio: 1 });
    await expect(pickers.last()).toBeInViewport({ ratio: 1 });
  });
});
