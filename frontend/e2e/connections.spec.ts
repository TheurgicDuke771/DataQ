import { expect, test } from '@playwright/test';

// Reads the seeded demo connections through the real API (proxy → api → DB).
// Names/labels come from backend/scripts/demo_data.py.
test.describe('Connections page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/connections');
    await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();
  });

  test('lists the seeded connections grouped by type', async ({ page }) => {
    // A datasource and an orchestration provider both render — the page shows
    // every connection type, grouped under its type heading.
    await expect(page.getByText('snowflake-analytics').first()).toBeVisible();
    await expect(page.getByText('s3-datalake').first()).toBeVisible();
    await expect(page.getByText('airflow-dags').first()).toBeVisible();

    // The type-section headings (CONNECTION_TYPE_LABELS) are present.
    await expect(page.getByRole('heading', { name: 'Snowflake', level: 5 })).toBeVisible();
  });

  test('"Test all" kicks off a connectivity test on every connection', async ({ page }) => {
    const testAll = page.getByRole('button', { name: 'Test all' });
    await expect(testAll).toBeEnabled();
    await testAll.click();

    // With placeholder creds the tests fail-soft — we assert the health signal
    // *appears* (testing→failed), not that connectivity succeeds. Either a
    // transient "testing…" or a settled "failed/healthy" badge proves the
    // per-card health path ran end-to-end.
    await expect(page.getByText(/testing…|failed|healthy/).first()).toBeVisible({
      timeout: 20_000,
    });
  });
});
