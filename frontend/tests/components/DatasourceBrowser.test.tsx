import { AxiosError, AxiosHeaders } from 'axios';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  browseCatalog,
  browseFiles,
  type CatalogBrowse,
  type FileBrowse,
} from '../../src/api/connections';
import {
  CatalogBrowserButton,
  FileBrowserButton,
} from '../../src/components/suites/DatasourceBrowser';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, browseCatalog: vi.fn(), browseFiles: vi.fn() };
});

const mockCatalog = vi.mocked(browseCatalog);
const mockFiles = vi.mocked(browseFiles);

afterEach(() => {
  mockCatalog.mockReset();
  mockFiles.mockReset();
});

function level(
  lvl: CatalogBrowse['level'],
  names: string[],
  overrides: Partial<CatalogBrowse> = {},
): CatalogBrowse {
  return {
    level: lvl,
    catalog: null,
    schema: null,
    entries: names.map((name) => ({ name, selectable: true })),
    truncated: false,
    limit: 200,
    ...overrides,
  };
}

function folder(prefix: string, folders: string[], files: string[] = []): FileBrowse {
  return {
    root: 'landing',
    prefix,
    folders,
    files: files.map((path) => ({ path, size: 2048, last_modified: null })),
    truncated: false,
    limit: 200,
  };
}

function failure(status: number, message: string, detail: Record<string, unknown> = {}) {
  const err = new AxiosError(message);
  err.response = {
    status,
    statusText: '',
    data: { error: { code: 'browse_failed', message, detail } },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return err;
}

/** A promise the test resolves by hand, to hold a request in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe('CatalogBrowserButton', () => {
  it('walks catalog → schema → table and hands back all three names', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    mockCatalog
      .mockResolvedValueOnce(level('catalog', ['dataq_retail', 'workspace']))
      .mockResolvedValueOnce(level('schema', ['gold', 'silver'], { catalog: 'dataq_retail' }))
      .mockResolvedValueOnce(
        level('table', ['daily_revenue'], { catalog: 'dataq_retail', schema: 'gold' }),
      );
    render(<CatalogBrowserButton connectionId="c1" onPick={onPick} />);

    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    await user.click(await screen.findByRole('button', { name: /dataq_retail/ }));
    await user.click(await screen.findByRole('button', { name: /gold/ }));
    await user.click(await screen.findByRole('button', { name: /daily_revenue/ }));

    expect(mockCatalog.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ['c1', { catalog: undefined, schema: undefined }],
      ['c1', { catalog: 'dataq_retail', schema: undefined }],
      ['c1', { catalog: 'dataq_retail', schema: 'gold' }],
    ]);
    expect(onPick).toHaveBeenCalledWith({
      catalog: 'dataq_retail',
      schema: 'gold',
      table: 'daily_revenue',
    });
  });

  it('goes back up through the breadcrumb', async () => {
    const user = userEvent.setup();
    mockCatalog
      .mockResolvedValueOnce(level('catalog', ['dataq_retail']))
      .mockResolvedValueOnce(level('schema', ['gold'], { catalog: 'dataq_retail' }))
      .mockResolvedValueOnce(level('catalog', ['dataq_retail', 'other']));
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    await user.click(await screen.findByRole('button', { name: /dataq_retail/ }));
    await screen.findByRole('button', { name: /gold/ });
    await user.click(screen.getByRole('button', { name: 'Catalogs' }));

    expect(await screen.findByRole('button', { name: /other/ })).toBeInTheDocument();
    expect(mockCatalog.mock.calls[2][1]).toEqual({ catalog: undefined, schema: undefined });
  });

  it('says a truncated level is only the first page, naming the limit', async () => {
    const user = userEvent.setup();
    mockCatalog.mockResolvedValueOnce(level('catalog', ['a', 'b'], { truncated: true, limit: 2 }));
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));

    expect(
      await screen.findByText('Showing the first 2 catalogs — there are more at this level.'),
    ).toBeInTheDocument();
  });

  it('does not claim completeness when the level is not truncated', async () => {
    const user = userEvent.setup();
    mockCatalog.mockResolvedValueOnce(level('catalog', ['a']));
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    await screen.findByRole('button', { name: /^.*a$/ });
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it('lists a name DataQ cannot target without letting it be picked', async () => {
    const user = userEvent.setup();
    mockCatalog.mockResolvedValueOnce({
      ...level('catalog', []),
      entries: [
        { name: 'good_one', selectable: true },
        { name: 'has-hyphen', selectable: false },
      ],
    });
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));

    await screen.findByRole('button', { name: /good_one/ });
    expect(screen.getByText('has-hyphen')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /has-hyphen/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Can't be picked/)).toBeInTheDocument();
  });

  it('shows an honest empty state scoped to what the credential can see', async () => {
    const user = userEvent.setup();
    mockCatalog.mockResolvedValueOnce(level('catalog', []));
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    expect(
      await screen.findByText("No catalogs are visible to this connection's credential."),
    ).toBeInTheDocument();
  });

  it("surfaces the backend's classified reason on a listing failure", async () => {
    const user = userEvent.setup();
    mockCatalog.mockRejectedValueOnce(
      failure(502, 'the datasource catalog could not be listed', {
        reason: 'The credential was rejected.',
      }),
    );
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));

    expect(await screen.findByText("Couldn't list the datasource")).toBeInTheDocument();
    expect(
      screen.getByText('the datasource catalog could not be listed The credential was rejected.'),
    ).toBeInTheDocument();
  });

  it('aborts a superseded request and never shows its late answer', async () => {
    const user = userEvent.setup();
    const slow = deferred<CatalogBrowse>();
    mockCatalog
      .mockResolvedValueOnce(level('catalog', ['dataq_retail']))
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(level('catalog', ['dataq_retail', 'fresh']));
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    await user.click(await screen.findByRole('button', { name: /dataq_retail/ }));
    // Navigate away while the schema listing is still in flight.
    await user.click(screen.getByRole('button', { name: 'Catalogs' }));
    await screen.findByRole('button', { name: /fresh/ });

    const staleSignal = mockCatalog.mock.calls[1][2] as AbortSignal;
    expect(staleSignal.aborted).toBe(true);
    slow.resolve(level('schema', ['stale_schema'], { catalog: 'dataq_retail' }));
    await Promise.resolve();
    expect(screen.queryByRole('button', { name: /stale_schema/ })).not.toBeInTheDocument();
  });

  it('starts from the top again each time it is reopened', async () => {
    const user = userEvent.setup();
    mockCatalog.mockImplementation(async (_id, params) =>
      params.catalog
        ? level('schema', ['gold'], { catalog: params.catalog })
        : level('catalog', ['dataq_retail']),
    );
    render(<CatalogBrowserButton connectionId="c1" onPick={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));
    await user.click(await screen.findByRole('button', { name: /dataq_retail/ }));
    await screen.findByRole('button', { name: /gold/ });
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await user.click(screen.getByRole('button', { name: /Browse catalog/ }));

    expect(await screen.findByRole('button', { name: /dataq_retail/ })).toBeInTheDocument();
    expect(mockCatalog.mock.calls.at(-1)?.[1]).toEqual({ catalog: undefined, schema: undefined });
  });
});

describe('FileBrowserButton', () => {
  it('drills into folders and picks a file by its full key', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    mockFiles
      .mockResolvedValueOnce(folder('', ['raw/'], ['_manifest.json']))
      .mockResolvedValueOnce(folder('raw/', [], ['raw/orders_2026.csv']));
    render(<FileBrowserButton connectionId="c1" rootLabel="landing" mode="file" onPick={onPick} />);

    await user.click(screen.getByRole('button', { name: /Browse files/ }));
    await user.click(await screen.findByRole('button', { name: /raw\// }));
    await user.click(await screen.findByRole('button', { name: /orders_2026\.csv/ }));

    expect(mockFiles.mock.calls.map((c) => c[1])).toEqual([{ prefix: '' }, { prefix: 'raw/' }]);
    expect(onPick).toHaveBeenCalledWith('raw/orders_2026.csv');
  });

  it('in folder mode offers the current folder, not the files in it', async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    mockFiles
      .mockResolvedValueOnce(folder('', ['raw/']))
      .mockResolvedValueOnce(folder('raw/', ['raw/2026/'], ['raw/a.csv']));
    render(
      <FileBrowserButton connectionId="c1" rootLabel="landing" mode="folder" onPick={onPick} />,
    );

    await user.click(screen.getByRole('button', { name: /Browse folders/ }));
    await user.click(await screen.findByRole('button', { name: /raw\// }));
    await screen.findByRole('button', { name: /2026\// });

    expect(screen.getByText('a.csv')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /a\.csv/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Use raw/' }));
    expect(onPick).toHaveBeenCalledWith('raw/');
  });

  it('names the container as the breadcrumb root and navigates back to it', async () => {
    const user = userEvent.setup();
    mockFiles
      .mockResolvedValueOnce(folder('', ['raw/']))
      .mockResolvedValueOnce(folder('raw/', ['raw/deep/']))
      .mockResolvedValueOnce(folder('raw/deep/', []))
      .mockResolvedValueOnce(folder('raw/', ['raw/deep/']));
    render(
      <FileBrowserButton connectionId="c1" rootLabel="landing" mode="file" onPick={vi.fn()} />,
    );

    await user.click(screen.getByRole('button', { name: /Browse files/ }));
    await user.click(await screen.findByRole('button', { name: /raw\// }));
    await user.click(await screen.findByRole('button', { name: /deep\// }));
    expect(await screen.findByText('Nothing under this folder.')).toBeInTheDocument();

    const crumbs = screen.getByRole('navigation');
    await user.click(within(crumbs).getByRole('button', { name: 'raw' }));
    await screen.findByRole('button', { name: /deep\// });
    expect(mockFiles.mock.calls.at(-1)?.[1]).toEqual({ prefix: 'raw/' });
    expect(within(crumbs).getByRole('button', { name: 'landing' })).toBeInTheDocument();
  });

  it('shows a folder the backend would refuse to list, by its full key, without opening it', async () => {
    const user = userEvent.setup();
    mockFiles.mockResolvedValueOnce(folder('', ['exports//', '/', 'raw/']));
    render(
      <FileBrowserButton connectionId="c1" rootLabel="landing" mode="folder" onPick={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: /Browse folders/ }));

    await screen.findByRole('button', { name: /raw\// });
    expect(screen.getByText('exports//')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /exports/ })).not.toBeInTheDocument();
    expect(screen.getAllByText(/Can't be opened here/)).toHaveLength(2);
    // At the top level the footer names no container/bucket it can't know is right.
    expect(
      screen.getByRole('button', { name: 'Use the top level (no prefix)' }),
    ).toBeInTheDocument();
    expect(mockFiles).toHaveBeenCalledTimes(1);
  });

  it('says a truncated folder listing is only the first page', async () => {
    const user = userEvent.setup();
    mockFiles.mockResolvedValueOnce({ ...folder('', ['a/']), truncated: true, limit: 1 });
    render(
      <FileBrowserButton connectionId="c1" rootLabel="landing" mode="file" onPick={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: /Browse files/ }));
    expect(
      await screen.findByText('Showing the first 1 entries — there are more at this level.'),
    ).toBeInTheDocument();
  });
});
