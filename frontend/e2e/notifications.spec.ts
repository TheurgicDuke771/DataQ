import { expect, test } from '@playwright/test';

// NotificationsPanel on the seeded suite's detail page: per-suite alerting
// config (W6 — fronts notification_service). Sets a deterministic state
// (enabled + warn threshold), saves, and proves it survives a reload. The
// Teams webhook stays untouched — it's write-only (the API never returns it)
// and writing one would push a secret into the store; the spec only asserts
// the write-only affordance renders.
test.describe('Suite notifications panel', () => {
  const card = (page: import('@playwright/test').Page) =>
    page.locator('.ant-card').filter({ hasText: 'run outcomes to Microsoft Teams' });

  const openSuite = async (page: import('@playwright/test').Page) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await expect(card(page).getByText('Notifications', { exact: true })).toBeVisible();
  };

  test('configure threshold routing and persist it across a reload', async ({ page }) => {
    await openSuite(page);
    const panel = card(page);

    // Snapshot the config so the spec doesn't permanently mutate the seeded
    // suite (alerting behavior for every later dev-stack run); restored below.
    const suiteId = page.url().match(/suites\/([0-9a-f-]+)/)?.[1];
    const before = await (await page.request.get(`/api/v1/suites/${suiteId}/notifications`)).json();

    // Deterministic target state: enabled + "On warn and worse".
    const enable = panel.getByRole('switch', { name: 'Enable notifications' });
    if (!(await enable.isChecked())) {
      await enable.click();
    }
    // Keyboard-only selection: rc-virtual-list parks option nodes in an
    // off-viewport measurement container, so clicking an option by role is
    // flaky (element "outside of the viewport"). On open the highlight sits
    // on the CURRENT value, so compute the arrow-key delta to the target.
    const OPTIONS = ['On fail / critical', 'On warn and worse', 'Always (every run)'];
    const target = OPTIONS.indexOf('On warn and worse');
    // The select renders its current value as visible text in the panel;
    // default (no config yet) is the first option.
    let current = 0;
    for (let i = 0; i < OPTIONS.length; i++) {
      if ((await panel.getByText(OPTIONS[i], { exact: true }).count()) > 0) {
        current = i;
        break;
      }
    }

    const threshold = panel.getByRole('combobox');
    await threshold.click();
    await expect(page.locator('.ant-select-dropdown').last()).toBeVisible();
    for (let i = 0; i < Math.abs(target - current); i++) {
      await threshold.press(target > current ? 'ArrowDown' : 'ArrowUp');
    }
    await threshold.press('Enter');

    await panel.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Notifications saved')).toBeVisible();

    // Reload → the form re-seeds from the API with the saved values.
    await page.reload();
    await expect(card(page).getByText('On warn and worse')).toBeVisible();
    await expect(card(page).getByRole('switch', { name: 'Enable notifications' })).toBeChecked();

    // Restore the pre-test config (webhook omitted = unchanged).
    const restored = await page.request.put(`/api/v1/suites/${suiteId}/notifications`, {
      data: { enabled: before.enabled, alert_on: before.alert_on },
    });
    expect(restored.ok()).toBe(true);
  });

  test('webhook is a write-only secret affordance', async ({ page }) => {
    await openSuite(page);
    const panel = card(page);

    // The field never echoes a stored URL — just a set/not-set tag + password
    // input. (Writing one is out of scope: it lands a secret in the store.)
    // Neither the seed nor any spec writes a webhook, so the tag is
    // deterministically 'not set' — pin it so a has_webhook regression fails.
    await expect(panel.getByText('Teams webhook')).toBeVisible();
    await expect(panel.getByLabel('Teams webhook URL')).toBeVisible();
    await expect(panel.getByText('not set', { exact: true })).toBeVisible();
  });
});
