import { expect, test } from '@playwright/test';
import { heading, shot } from './shot';

// One test per screenshot so a moved selector loses one image, not the set.
// Names are stable: docs pages reference them by name.

test('dashboard', async ({ page }) => {
  await page.goto('/dashboard');
  await heading(page, /Dashboard|Overview/);
  await shot(page, 'dashboard');
});

test('connections list', async ({ page }) => {
  await page.goto('/connections');
  await heading(page, 'Connections');
  await shot(page, 'connections-list');
});

test('connection type picker + Snowflake form', async ({ page }) => {
  await page.goto('/connections/new');
  await heading(page, 'New connection');
  await shot(page, 'connection-type-picker');
  await page.getByText('Snowflake', { exact: true }).click();
  await expect(page.getByLabel('Name')).toBeVisible();
  await shot(page, 'connection-form-snowflake');
});

test('suites list', async ({ page }) => {
  await page.goto('/suites');
  await heading(page, 'Suites');
  await shot(page, 'suites-list');
});

test('suite detail', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await heading(page, 'Orders quality', 4);
  await expect(page.getByText('order_id not null')).toBeVisible();
  await shot(page, 'suite-detail');
});

test('check editor — type picker', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await page.getByRole('button', { name: 'Add check' }).click();
  await expect(page.getByText('Column values', { exact: true })).toBeVisible();
  await shot(page, 'check-editor-picker');
});

test('check editor — expectation form', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await page.getByRole('button', { name: 'Add check' }).click();
  await page.getByText('Column values', { exact: true }).click();
  await page.getByText('Column values not null', { exact: true }).click();
  await page.getByLabel('Name').fill('order_id not null');
  await page.getByLabel('Column', { exact: true }).fill('order_id');
  await expect(page.getByRole('button', { name: 'Dry-run preview' })).toBeEnabled();
  await shot(page, 'check-editor-expectation');
});

test('check editor — freshness monitor', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await page.getByRole('button', { name: 'Add check' }).click();
  await page.getByText('Freshness', { exact: true }).click();
  await page.getByText(/How stale is the target/).click();
  await page.getByLabel('Name').fill('orders arrive hourly');
  await page.getByLabel('Timestamp column').fill('order_ts');
  await page.getByLabel('Fail ≥').fill('26');
  await shot(page, 'check-editor-freshness');
});

test('results list', async ({ page }) => {
  await page.goto('/results');
  await heading(page, 'Results');
  await expect(page.getByText('Orders quality').first()).toBeVisible();
  await shot(page, 'results-list');
});

test('run detail', async ({ page }) => {
  await page.goto('/results');
  await page.getByText('Orders quality').first().click();
  const detail = page.getByTestId('rd-screen');
  await expect(detail.getByText('order_id not null')).toBeVisible();
  await shot(page, 'run-detail');
});

test('assets', async ({ page }) => {
  await page.goto('/assets');
  await heading(page, 'Assets');
  await shot(page, 'assets-list');
});

test('asset detail', async ({ page }) => {
  await page.goto('/assets');
  await page
    .getByText(/ORDERS/)
    .first()
    .click();
  await expect(page).toHaveURL(/\/assets\//);
  await page.waitForLoadState('networkidle');
  await shot(page, 'asset-detail');
});

test('profile & API keys', async ({ page }) => {
  await page.goto('/profile');
  await heading(page, /Profile/);
  await shot(page, 'profile');
});

test('admin', async ({ page }) => {
  await page.goto('/admin');
  await heading(page, /Admin/);
  await shot(page, 'admin');
});

test('settings', async ({ page }) => {
  await page.goto('/settings');
  await heading(page, /Settings/);
  await shot(page, 'settings');
});

test('settings — notification channels', async ({ page }) => {
  await page.goto('/settings');
  await heading(page, /Settings/);
  await page.getByRole('tab', { name: 'Notifications' }).click();
  await page.waitForLoadState('networkidle');
  await shot(page, 'settings-notifications');
});

test('suite — notifications panel', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await heading(page, 'Orders quality', 4);
  const panel = page.getByText('Notifications', { exact: true }).first();
  await panel.scrollIntoViewIfNeeded();
  await shot(page, 'suite-notifications');
});

test('suite — schedules panel', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await heading(page, 'Orders quality', 4);
  const panel = page.getByText('Schedules', { exact: true }).first();
  await panel.scrollIntoViewIfNeeded();
  await shot(page, 'suite-schedules');
});

test('admin — LLM provider settings', async ({ page }) => {
  await page.goto('/admin');
  await heading(page, /Admin/);
  await page
    .getByText('LLM provider', { exact: true })
    .evaluate((el) => el.closest('.ant-card')?.scrollIntoView({ block: 'start' }));
  await expect(page.getByRole('button', { name: 'Test' })).toBeVisible();
  await shot(page, 'admin-llm-settings');
});

test('asset — incident evidence card', async ({ page }) => {
  await page.goto('/assets');
  await page.getByRole('treeitem', { name: /^ORDERS dev/ }).click();
  await expect(page).toHaveURL(/\/assets\//);
  await page.getByRole('button', { name: 'View' }).first().click();
  await expect(page.getByText('Incident evidence')).toBeVisible();
  await shot(page, 'incident-evidence');
});
