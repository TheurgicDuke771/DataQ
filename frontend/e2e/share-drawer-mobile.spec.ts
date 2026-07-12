import { expect, test } from '@playwright/test';

// Share drawer on a phone (#829). The drawer itself was always a proper overlay —
// antd clamps it to the viewport — but its add-collaborator row could not shrink
// (an antd Select has a min-content width that `flex: 1` alone won't shrink past),
// so the row demanded more width than the drawer had and pushed the "Add" button
// clean off the right edge. A suite was therefore **unshareable from mobile**: you
// could pick a person and a permission but never commit the grant.
//
// These assert the two halves of that: nothing overflows, and the button a user
// must actually reach is inside the viewport and clickable.
test.describe('share drawer (390px)', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the Add button is reachable and the drawer does not overflow', async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);

    await page.getByRole('button', { name: 'Share', exact: true }).click();
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();

    // It is an overlay: it sits above the page behind a scrim, and the drawer panel
    // is clamped to the viewport rather than running off it.
    await expect(page.locator('.ant-drawer-mask')).toBeVisible();
    const panel = page.locator('.ant-drawer-content-wrapper');
    await expect
      .poll(async () => {
        const box = await panel.boundingBox();
        return box ? Math.round(box.x + box.width) : 0;
      })
      .toBeLessThanOrEqual(390);

    // The regression itself: the drawer body must not scroll sideways. Before the
    // fix this was scrollWidth 407 vs clientWidth 390.
    const bodyOverflow = await page
      .locator('.ant-drawer-body')
      .evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(bodyOverflow).toBeLessThanOrEqual(0);

    // And the button itself is on-screen. `toBeInViewport` is the assertion that
    // would have caught this: the button existed and was "visible" to the DOM, it
    // was simply painted outside the screen at x=407.
    const add = drawer.getByRole('button', { name: 'Add', exact: true });
    await expect(add).toBeInViewport();

    // All three controls of the row survive the reflow — wrapping must not hide the
    // permission picker to make room.
    await expect(drawer.getByPlaceholder('Search by email or name')).toBeInViewport();
    await expect(drawer.getByText('Can view')).toBeInViewport();
  });
});
