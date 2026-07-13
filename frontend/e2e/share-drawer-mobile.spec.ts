import { expect, test } from '@playwright/test';

// Share drawer on a phone (#829). The drawer itself was always a proper overlay —
// antd clamps it to the viewport — but its add-collaborator row could not shrink
// (an antd Select has a min-content width that `flex: 1` alone won't shrink past),
// so the row demanded more width than the drawer had and pushed the "Add" button
// clean off the right edge. A suite was therefore **unshareable from mobile**: you
// could pick a person and a permission but never commit the grant.
//
// Two traps this spec has to dodge, or it would pass against the unfixed code:
//
//  1. The row is behind `AsyncBody` — while the share list is loading the drawer
//     body holds nothing but a <Spin>, which of course doesn't overflow. Every
//     measurement below therefore waits for the row itself to be on screen first.
//  2. `toBeInViewport()` defaults to `ratio: 0` — "intersects the viewport at all".
//     Pre-fix the Add button spanned x≈348→407 on a 390px viewport, i.e. ~71% of it
//     was visible, so the default would have been GREEN on the bug. It must be
//     `ratio: 1`: the whole button, or it isn't reachable.
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

    // Both pickers a user needs to actually grant access survive the reflow —
    // wrapping must not push the permission picker out to make room for the button.
    //
    // Located structurally (the two `.ant-select`s that are the Add button's
    // siblings) rather than by text or placeholder: an antd Select renders NO
    // `placeholder` attribute — the placeholder is a separate span — so
    // `getByPlaceholder` matches nothing, and matching the permission label by text
    // would go ambiguous the moment this suite has a `view` share, since `ShareRow`
    // renders the same labels.
    const addRow = add.locator('xpath=..');
    const pickers = addRow.locator('.ant-select');
    await expect(pickers).toHaveCount(2);
    await expect(pickers.first()).toBeInViewport({ ratio: 1 });
    await expect(pickers.last()).toBeInViewport({ ratio: 1 });
  });
});
