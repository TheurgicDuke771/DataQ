import { expect, test } from '@playwright/test';

// Keyboard traversal, in a real browser: jsdom has no focus order, no layout and no scrolling,
// so none of this can be asserted there.
test.describe('Keyboard navigation', () => {
  test('the first Tab stop is a skip link that lands focus on the page content', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Monitoring Dashboard' })).toBeVisible();
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.locator('main#dq-main')).toBeFocused();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('a run is reachable and openable from the Results table without a mouse', async ({
    page,
  }) => {
    await page.goto('/results');
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await expect(link).toBeVisible();
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await expect(page.getByTestId('rd-screen')).toBeVisible();
    // Focus followed the navigation instead of staying on a link that no longer exists.
    await expect(page.locator('main#dq-main')).toBeFocused();
  });

  test('clicking a row still opens the run, once — Back returns to the list', async ({ page }) => {
    await page.goto('/results');
    await page.locator('tr.ant-table-row a.dq-row-link').first().click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/results$/);
  });

  test('an asset is openable from the Assets table by keyboard', async ({ page }) => {
    await page.goto('/assets');
    await page.getByText('All assets', { exact: true }).click();
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await link.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/assets\/[0-9a-f-]+$/);
  });

  test('clicking a cell that is not the link still opens the run, once', async ({ page }) => {
    await page.goto('/results');
    await page.locator('tr.ant-table-row').first().locator('td').nth(3).click();
    await expect(page).toHaveURL(/\/results\/[0-9a-f-]+$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/results$/);
  });

  test('a suite opens from the master list by keyboard, and focus stays in the list', async ({
    page,
  }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').first().click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    const other = page.locator('.dq-suite-row a.dq-row-link', { hasText: 'Orders volume' });
    await other.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Orders volume', level: 4 })).toBeVisible();
    // Selecting a suite navigates without going anywhere: the list is still there, so focus
    // must not be taken off it.
    await expect(other).toBeFocused();
    await expect(other).toHaveAttribute('aria-current', 'page');
  });

  test('switching admin tabs keeps focus on the tab bar', async ({ page }) => {
    await page.goto('/admin/overview');
    const members = page.getByRole('tab', { name: 'Members' });
    await members.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/admin\/members$/);
    await expect(members).toBeFocused();
  });

  test('a truncated asset name keeps a whole focus ring', async ({ page }) => {
    await page.goto('/assets');
    await page.getByText('All assets', { exact: true }).click();
    const link = page.locator('tr.ant-table-row a.dq-row-link').first();
    await link.focus();
    // An `overflow: hidden` ancestor clips the ring; the link must not sit inside one.
    const clipped = await link.evaluate((el) => {
      const cell = el.closest('td');
      for (let a = el.parentElement; a && a !== cell; a = a.parentElement) {
        if (getComputedStyle(a).overflow !== 'visible') return a.className || a.tagName;
      }
      return null;
    });
    expect(clipped).toBeNull();
  });

  test('every navigation retitles the document, and a run keeps its own richer title', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    await expect(page).toHaveTitle('Dashboard · DataQ');
    await page.getByRole('link', { name: 'Results' }).click();
    await expect(page).toHaveTitle('Results · DataQ');
    await page.locator('tr.ant-table-row a.dq-row-link').first().click();
    await expect(page.getByTestId('rd-screen')).toBeVisible();
    // RunDetail names the suite (it doubles as the Save-as-PDF filename) — more specific wins.
    await expect(page).toHaveTitle(/Orders/);
    await page.goBack();
    await expect(page).toHaveTitle('Results · DataQ');
    await page.goto('/admin/members');
    await expect(page).toHaveTitle('Members · Admin · DataQ');
  });
});

// Dialogs and saves on the v1.3 surfaces (CONTRIBUTING rule 44): a dialog hands focus back to
// what opened it, and a save that removes or disables the focused control leaves focus nearby
// and says what happened.
test.describe('Keyboard: dialogs and saves keep focus', () => {
  test('a calendar entry dialog returns focus to the button that opened it', async ({
    page,
    request,
  }) => {
    const name = `kb season ${Date.now()}`;
    const res = await request.post('/api/v1/admin/seasonal-periods', {
      data: { name, kind: 'recurring', start_month: 3, start_day: 1, end_month: 3, end_day: 5 },
    });
    const id = ((await res.json()) as { id: string }).id;
    try {
      await page.goto('/admin/calendar');
      const create = page.getByRole('button', { name: /New entry/ });
      await create.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog', { name: 'New calendar entry' });
      await expect(dialog.getByRole('radiogroup', { name: 'Repeats' })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(create).toBeFocused();

      const edit = page.getByRole('button', { name: `Edit ${name}` });
      await edit.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('dialog', { name: `Edit “${name}”` })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(edit).toBeFocused();
    } finally {
      await request.delete(`/api/v1/admin/seasonal-periods/${id}`);
    }
  });

  test('promoting a legacy destination starts on the name and ends inside the panel', async ({
    page,
    request,
  }) => {
    const suites = (await (await request.get('/api/v1/suites')).json()) as {
      id: string;
      name: string;
    }[];
    const suite = suites.find((s) => s.name === 'Orders quality');
    if (!suite) throw new Error('seeded suite "Orders quality" not found');
    let promoted = false;
    // A suite cannot be given an inline destination any more, so the legacy one is answered here.
    await page.route(`**/api/v1/suites/${suite.id}/notifications`, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const body = (await (await route.fetch()).json()) as Record<string, unknown>;
      await route.fulfill({ json: { ...body, has_webhook: !promoted } });
    });
    await page.route(`**/api/v1/suites/${suite.id}/notifications/promote`, async (route) => {
      promoted = true;
      await route.fulfill({
        json: {
          channel: { id: 'c-kb', name: 'kb channel', kind: 'teams' },
          workspace_default_now_applies: false,
        },
      });
    });
    await page.goto(`/suites/${suite.id}`);
    const trigger = page.getByRole('button', { name: 'Promote to channel: Teams webhook' });
    await trigger.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Promote to channel' });
    await expect(dialog.getByRole('textbox', { name: 'Channel name' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(dialog.getByRole('textbox', { name: 'Channel name' })).toBeFocused();
    await page.keyboard.type('kb channel');
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('alert').filter({ hasText: 'Promoted to "kb channel"' }),
    ).toBeVisible();
    // The promote removed the card the trigger sat in.
    await expect(trigger).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => document.activeElement !== document.body))
      .toBe(true);
    await expect(page.locator(':focus')).toContainText('Alerting');
  });

  test('the usage-telemetry switch keeps focus through each save', async ({ page, request }) => {
    const before = (await (await request.get('/api/v1/admin/telemetry')).json()) as {
      stored: boolean;
    };
    try {
      await page.goto('/admin/settings');
      const toggle = page.getByRole('switch', { name: 'Usage telemetry' });
      await toggle.focus();
      await page.keyboard.press('Space');
      await expect(
        page.getByRole('alert').filter({ hasText: /Usage telemetry turned/ }),
      ).toBeVisible();
      await expect(toggle).toBeEnabled();
      await expect(toggle).toBeFocused();
      await expect(toggle).toHaveAttribute('aria-checked', String(!before.stored));
      await page.keyboard.press('Space');
      await expect(toggle).toHaveAttribute('aria-checked', String(before.stored));
      await expect(toggle).toBeFocused();
    } finally {
      await request.put('/api/v1/admin/telemetry', { data: { enabled: before.stored } });
    }
  });

  test("a table's coverage save is announced and keeps focus in the block", async ({
    page,
    request,
  }) => {
    const assets = (await (await request.get('/api/v1/assets')).json()) as { id: string }[];
    // Not the first asset: assets.spec.ts overrides that one.
    const assetId = assets[assets.length - 1].id;
    try {
      await page.goto(`/assets/${assetId}`);
      await page
        .getByRole('button', { name: /Sensitivity of this table’s automatic checks/ })
        .focus();
      await page.keyboard.press('Enter');
      const block = page.getByTestId('coverage-sensitivity');
      await block.getByRole('switch', { name: 'Set for this table: Row count thresholds' }).focus();
      await page.keyboard.press('Space');
      await block.getByRole('button', { name: 'Save' }).focus();
      await page.keyboard.press('Enter');
      await expect(
        page.getByRole('alert').filter({ hasText: 'Saved for this table' }),
      ).toBeVisible();
      await expect
        .poll(() =>
          page.evaluate(
            () => !!document.activeElement?.closest('[data-testid="coverage-sensitivity"]'),
          ),
        )
        .toBe(true);
    } finally {
      await request.patch(`/api/v1/assets/${assetId}`, { data: { auto_coverage_config: null } });
    }
  });

  test('dismissing a detection moves focus to the restore button that replaces it', async ({
    page,
    request,
  }) => {
    const assets = (await (await request.get('/api/v1/assets')).json()) as { id: string }[];
    const assetId = assets[assets.length - 1].id;
    let dismissed = false;
    // No profile runs in this lane, so the detection (and its dismissal) are answered here.
    await page.route(`**/api/v1/assets/${assetId}`, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const body = (await (await route.fetch()).json()) as Record<string, unknown>;
      await route.fulfill({
        json: {
          ...body,
          detected_classifications: [
            {
              column: 'contact',
              detector: 'email',
              effect: dismissed ? 'dismissed' : 'masks',
              first_detected_at: '2026-10-01T00:00:00Z',
              last_detected_at: '2026-10-02T00:00:00Z',
              dismissed,
              dismissed_at: dismissed ? '2026-10-03T00:00:00Z' : null,
            },
          ],
          detected_classifications_refreshed_at: '2026-10-02T00:00:00Z',
        },
      });
    });
    await page.route(`**/api/v1/assets/${assetId}/classifications`, async (route) => {
      dismissed = true;
      await route.fulfill({ json: {} });
    });
    await page.goto(`/assets/${assetId}`);
    const panel = page.getByTestId('detected-masking-panel');
    await expect(panel.getByRole('columnheader', { name: 'Actions' })).toBeAttached();
    await panel.getByRole('button', { name: 'Dismiss: contact' }).click();
    await page.getByRole('tooltip').getByRole('button', { name: 'Dismiss', exact: true }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'contact: detection dismissed' }),
    ).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Restore masking: contact' })).toBeFocused();
  });

  test("a disabled check's badge explains itself to keyboard focus", async ({ page, request }) => {
    const suites = (await (await request.get('/api/v1/suites')).json()) as {
      id: string;
      name: string;
    }[];
    const suite = suites.find((s) => s.name === 'Orders volume');
    if (!suite) throw new Error('seeded suite "Orders volume" not found');
    const res = await request.post(`/api/v1/suites/${suite.id}/checks`, {
      data: {
        name: `kb disabled ${Date.now()}`,
        expectation_type: 'expect_column_values_to_not_be_null',
        config: { column: 'id' },
        enabled: false,
      },
    });
    expect(res.status()).toBe(201);
    const check = (await res.json()) as { id: string; enabled?: boolean };
    try {
      if (check.enabled !== false) {
        await request.patch(`/api/v1/suites/${suite.id}/checks/${check.id}`, {
          data: { enabled: false },
        });
      }
      await page.goto(`/suites/${suite.id}`);
      const badge = page.getByText('Disabled', { exact: true });
      await badge.focus();
      await expect(badge).toBeFocused();
      const tip = page.getByRole('tooltip').filter({ hasText: 'runs skip this check' });
      await expect(tip).toBeVisible();
      await expect(badge).toHaveAccessibleDescription(/runs skip this check/);
    } finally {
      await request.delete(`/api/v1/suites/${suite.id}/checks/${check.id}`);
    }
  });

  test('the command palette opens on Ctrl/⌘+K, moves by arrow keys and opens a suite on Enter', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: 'Monitoring Dashboard' })).toBeVisible();
    const trigger = page.getByRole('button', { name: /^Search/ });
    await trigger.focus();
    await page.keyboard.press('ControlOrMeta+k');
    const input = page.getByRole('combobox', { name: 'Search DataQ' });
    await expect(input).toBeFocused();
    await input.fill('Orders quality');
    const option = page.getByRole('option', { name: /Orders quality/ }).first();
    await expect(option).toBeVisible();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');
    await expect(option).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    // Focus moved to the destination page, not back to the header button.
    await expect(page.locator('main#dq-main')).toBeFocused();
  });

  test('Escape closes the command palette and returns focus to the button that opened it', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    const trigger = page.getByRole('button', { name: /^Search/ });
    await trigger.click();
    const input = page.getByRole('combobox', { name: 'Search DataQ' });
    await expect(input).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});
