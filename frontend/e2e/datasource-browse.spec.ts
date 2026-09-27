import { expect, type Page, test } from '@playwright/test';

/**
 * Run-target browse pickers on the suite edit page. The seeded connections point at fake hosts, so
 * the two `browse/*` listings are answered by `page.route` — what is under test is the UI walking
 * the levels, honouring `truncated`/`selectable`, and filling the typed fields, not a datasource.
 * Nothing is saved: the seeded suites' targets stay as they were.
 */

async function suiteId(page: Page, name: string): Promise<string> {
  const suites: { id: string; name: string }[] = await (
    await page.request.get('/api/v1/suites')
  ).json();
  const suite = suites.find((s) => s.name === name);
  expect(suite, `seeded "${name}" suite`).toBeDefined();
  return suite?.id ?? '';
}

test.describe('Run-target browse pickers', () => {
  test('Unity Catalog: catalog → schema → table fills the target fields', async ({ page }) => {
    const seen: string[] = [];
    await page.route('**/api/v1/connections/*/browse/catalog**', async (route) => {
      const url = new URL(route.request().url());
      const catalog = url.searchParams.get('catalog');
      const schema = url.searchParams.get('schema');
      seen.push(`${catalog ?? ''}/${schema ?? ''}`);
      const body = !catalog
        ? {
            level: 'catalog',
            catalog: null,
            schema: null,
            entries: [
              { name: 'dataq_retail', selectable: true },
              { name: 'odd-name', selectable: false },
            ],
            truncated: false,
            limit: 200,
          }
        : !schema
          ? {
              level: 'schema',
              catalog,
              schema: null,
              entries: [{ name: 'gold', selectable: true }],
              truncated: true,
              limit: 1,
            }
          : {
              level: 'table',
              catalog,
              schema,
              entries: [{ name: 'daily_revenue', selectable: true }],
              truncated: false,
              limit: 200,
            };
      await route.fulfill({ json: body });
    });

    await page.goto(`/suites/${await suiteId(page, 'Lakehouse events')}/edit`);
    await page.getByRole('button', { name: /Browse catalog/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Pick a table' });

    await expect(dialog.getByText('odd-name')).toBeVisible();
    await expect(dialog.getByRole('button', { name: /odd-name/ })).toHaveCount(0);
    await dialog.getByRole('button', { name: /dataq_retail/ }).click();
    await expect(dialog.getByText(/Showing the first 1 schemas/)).toBeVisible();
    await dialog.getByRole('button', { name: /gold/ }).click();
    await dialog.getByRole('button', { name: /daily_revenue/ }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByLabel('Catalog')).toHaveValue('dataq_retail');
    await expect(page.getByLabel('Schema (optional)')).toHaveValue('gold');
    await expect(page.getByLabel('Table')).toHaveValue('daily_revenue');
    expect(seen).toEqual(['/', 'dataq_retail/', 'dataq_retail/gold']);
  });

  test('S3: folder → file fills the file path', async ({ page }) => {
    await page.route('**/api/v1/connections/*/browse/files**', async (route) => {
      const prefix = new URL(route.request().url()).searchParams.get('prefix') ?? '';
      const body = prefix
        ? {
            root: 'acme-datalake',
            prefix,
            folders: [],
            files: [{ path: `${prefix}customers.csv`, size: 4096, last_modified: null }],
            truncated: false,
            limit: 200,
          }
        : {
            root: 'acme-datalake',
            prefix: '',
            folders: ['exports/'],
            files: [],
            truncated: false,
            limit: 200,
          };
      await route.fulfill({ json: body });
    });

    await page.goto(`/suites/${await suiteId(page, 'Customer files')}/edit`);
    await page.getByRole('button', { name: /Browse files/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Pick a file' });

    await expect(dialog.getByRole('navigation')).toContainText('acme-datalake');
    await dialog.getByRole('button', { name: /exports\// }).click();
    await dialog.getByRole('button', { name: /customers\.csv/ }).click();

    await expect(dialog).toBeHidden();
    await expect(page.getByLabel('File path')).toHaveValue('exports/customers.csv');
  });
});
