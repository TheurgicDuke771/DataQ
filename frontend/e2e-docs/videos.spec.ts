import { expect, test } from '@playwright/test';

// Short clips (≤20s) for the docs. Recorded at 1× so the mp4 stays small;
// scripts/docs/transcode-videos.sh turns the webm into mp4 + poster under
// docs/site/assets/videos/<title>.{mp4,jpg}. The test title IS the file name.

test.use({
  video: { mode: 'on', size: { width: 1440, height: 900 } },
  deviceScaleFactor: 1,
});

const beat = (ms = 900) => new Promise((r) => setTimeout(r, ms));

test('tour', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page.getByRole('heading', { name: /Dashboard/ })).toBeVisible();
  await beat(1800);
  for (const item of ['Assets', 'Connections', 'Suites', 'Results']) {
    await page.getByRole('link', { name: item }).click();
    await page.waitForLoadState('networkidle');
    await beat(1600);
  }
});

test('add-connection', async ({ page }) => {
  await page.goto('/connections');
  await expect(page.getByRole('heading', { name: 'Connections', level: 3 })).toBeVisible();
  await beat();
  await page.getByRole('button', { name: 'Add connection' }).click();
  await expect(page.getByRole('heading', { name: 'New connection' })).toBeVisible();
  await beat(1200);
  await page.getByText('Snowflake', { exact: true }).click();
  await expect(page.getByLabel('Name')).toBeVisible();
  await beat(600);
  await page.getByLabel('Name').pressSequentially('snowflake-prod', { delay: 60 });
  await page
    .getByLabel('Account', { exact: true })
    .pressSequentially('acme-xy12345', { delay: 60 });
  await page.getByLabel('User', { exact: true }).pressSequentially('DATAQ_READER', { delay: 60 });
  await page.getByLabel('Database', { exact: true }).pressSequentially('ANALYTICS', { delay: 60 });
  await beat(1500);
});

test('author-check', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
  await beat(1200);
  await page.getByRole('button', { name: 'Add check' }).click();
  await expect(page.getByText('Column values', { exact: true })).toBeVisible();
  await beat(1200);
  await page.getByText('Column values', { exact: true }).click();
  await beat(800);
  await page.getByText('Column values not null', { exact: true }).click();
  await beat(600);
  await page.getByLabel('Name').pressSequentially('customer_id not null', { delay: 60 });
  await page.getByLabel('Column', { exact: true }).pressSequentially('customer_id', { delay: 60 });
  await beat(800);
  await expect(page.getByRole('button', { name: 'Dry-run preview' })).toBeEnabled();
  await page.getByRole('button', { name: 'Dry-run preview' }).hover();
  await beat(1500);
});

test('read-results', async ({ page }) => {
  await page.goto('/results');
  await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toBeVisible();
  await beat(1500);
  await page.getByText('Orders quality').first().click();
  const detail = page.getByTestId('rd-screen');
  await expect(detail.getByText('order_id not null')).toBeVisible();
  await beat(1500);
  const rows = detail.getByRole('button', { name: /expand/i });
  if ((await rows.count()) > 0) {
    await rows.last().click();
    await beat(2000);
  }
});

test('wire-alert', async ({ page }) => {
  await page.goto('/suites');
  await page.getByText('Orders quality').click();
  await expect(page.getByRole('heading', { name: 'Orders quality', level: 4 })).toBeVisible();
  await beat(800);
  const notif = page.getByText(/Notifications/).first();
  await notif.scrollIntoViewIfNeeded();
  await beat(1500);
  await page.mouse.wheel(0, 500);
  await beat(2000);
});

test('configure-llm', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: /Admin/ })).toBeVisible();
  await beat(1000);
  await page.getByText('LLM provider', { exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByLabel('Model')).toBeVisible();
  await beat(1500);
  await page.getByLabel('Model').fill('');
  await page.getByLabel('Model').pressSequentially('qwen2.5:14b', { delay: 60 });
  await beat(600);
  await page.getByLabel('Base URL').fill('');
  await page.getByLabel('Base URL').pressSequentially('http://127.0.0.1:11434/v1', { delay: 40 });
  await beat(800);
  await page.getByRole('button', { name: 'Test' }).click();
  await expect(page.getByText(/ok|failed|ms/i).last()).toBeVisible({ timeout: 30_000 });
  await beat(2500);
});
