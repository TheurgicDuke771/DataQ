import { expect, test } from '@playwright/test';

// TriggersPanel on the seeded suite's detail page: bind an orchestrator
// pipeline (run-on-success, CLAUDE.md §4 — the one place a pipeline id meets a
// suite), toggle it, and remove it. The binding is provider-agnostic
// (`trigger_bindings`); the seeded ADF/Airflow connections make the providers
// real, and a unique pipeline id keeps the composite key (provider, id, env)
// collision-free across runs.
test.describe('Suite triggers panel', () => {
  const card = (page: import('@playwright/test').Page) =>
    page.locator('.ant-card').filter({ hasText: 'orchestrator pipeline' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/suites');
    await page.getByText('Orders quality').click();
    await expect(page).toHaveURL(/\/suites\/[0-9a-f-]+$/);
    await expect(card(page).getByText('Triggers', { exact: true })).toBeVisible();
  });

  test('bind a pipeline, disable it, then remove it', async ({ page }) => {
    const panel = card(page);
    const pipelineId = `e2e_pl_${Date.now()}`;

    // Provider + Env are antd Selects: focus the combobox, wait for the
    // dropdown, then Enter accepts the auto-highlighted FIRST option (an
    // ArrowDown first would move to the second — rc-select pre-highlights
    // option 0 when nothing is selected). First options: 'adf' / 'dev'.
    const provider = panel.getByLabel('Provider');
    await provider.click();
    await expect(page.locator('.ant-select-dropdown').last()).toBeVisible();
    await provider.press('Enter');

    await panel.getByPlaceholder('Pipeline / DAG id').fill(pipelineId);

    const env = panel.getByLabel('Env');
    await env.click();
    await expect(page.locator('.ant-select-dropdown').last()).toBeVisible();
    await env.press('Enter');

    await panel.getByRole('button', { name: 'Add' }).click();
    await expect(page.getByText(`${pipelineId}: trigger added`)).toBeVisible();

    // Listed with its provider label and enabled by default.
    const row = panel.locator('[role="listitem"]').filter({ hasText: pipelineId });
    await expect(row).toBeVisible();
    await expect(row.getByText('Azure Data Factory')).toBeVisible();
    await expect(row.getByRole('switch')).toBeChecked();

    // Disable (run-on-success stops firing without losing the binding).
    await row.getByRole('switch').click();
    await expect(page.getByText(`${pipelineId}: disabled`)).toBeVisible();

    // Remove — immediate (no confirm) — and the row disappears.
    await row.getByRole('button', { name: `Remove ${pipelineId}` }).click();
    await expect(page.getByText(`${pipelineId}: removed`)).toBeVisible();
    await expect(panel.locator('[role="listitem"]').filter({ hasText: pipelineId })).toHaveCount(0);
  });
});
