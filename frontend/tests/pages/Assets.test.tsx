import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type AssetListPage, type AssetSummary, listAssets } from '../../src/api/assets';
import { Assets } from '../../src/pages/Assets';

vi.mock('../../src/api/assets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/assets')>()),
  listAssets: vi.fn(),
}));
const mockList = vi.mocked(listAssets);

const ASSET: AssetSummary = {
  id: 'a1',
  namespace: 'snowflake://acct',
  name: 'ANALYTICS.PUBLIC.ORDERS',
  env: 'dev',
  description: null,
  owner_user_id: null,
  last_seen: '2026-07-01T10:00:00Z',
  suite_count: 2,
  worst_severity: 'fail',
  checks_total: 8,
  checks_passed: 6,
  last_run_at: '2026-07-01T09:00:00Z',
  has_failed_run: false,
  has_active_run: false,
  has_operational_error: false,
  has_cancelled_run: false,
  has_skip: false,
};

/** Build the `AssetListPage` the mocked `listAssets` resolves — `total`
 *  defaults to the fetched length (an untruncated fetch) unless overridden. */
function page(items: AssetSummary[], total?: number): AssetListPage {
  return { items, total: total ?? items.length };
}

afterEach(() => vi.clearAllMocks());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assets']}>
      <Routes>
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/:assetId" element={<div>detail for asset</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Assets page — tree view (default)', () => {
  it('defaults to the connection-rooted tree (namespace root, table leaf with health)', async () => {
    mockList.mockResolvedValue(page([ASSET]));
    renderPage();
    // Root = the OL namespace; leaf = the table segment (not the full dotted name),
    // with its env + health tags — the drill-down levels are expanded by default.
    // The root reads as a datasource now, not a raw OL namespace (#830).
    expect(await screen.findByText('Snowflake · acct')).toBeInTheDocument();
    expect(screen.getByText('ANALYTICS')).toBeInTheDocument();
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.getByText('ORDERS')).toBeInTheDocument();
    expect(screen.getByText('dev')).toBeInTheDocument();
    expect(screen.getByText('Failing')).toBeInTheDocument();
  });

  it('opens the detail when a leaf asset is selected', async () => {
    mockList.mockResolvedValue(page([ASSET]));
    renderPage();
    await userEvent.click(await screen.findByText('ORDERS'));
    expect(await screen.findByText('detail for asset')).toBeInTheDocument();
  });

  it('selecting a folder node does not navigate', async () => {
    mockList.mockResolvedValue(page([ASSET]));
    renderPage();
    // ANALYTICS is a database folder (no asset) — clicking it must not open a detail.
    await userEvent.click(await screen.findByText('ANALYTICS'));
    expect(screen.queryByText('detail for asset')).not.toBeInTheDocument();
  });

  it('groups assets under their datasource and drills into each schema', async () => {
    mockList.mockResolvedValue(
      page([
        ASSET,
        { ...ASSET, id: 'a2', name: 'ANALYTICS.PUBLIC.CUSTOMERS', env: 'dev' },
        { ...ASSET, id: 'a3', namespace: 's3://lake', name: 'raw/events.parquet', env: 'qa' },
      ]),
    );
    renderPage();
    // Two datasource roots.
    expect(await screen.findByText('Snowflake · acct')).toBeInTheDocument();
    expect(screen.getByText('S3 · lake')).toBeInTheDocument();
    // Both tables share the PUBLIC schema folder (merged), each a distinct leaf.
    expect(screen.getByText('ORDERS')).toBeInTheDocument();
    expect(screen.getByText('CUSTOMERS')).toBeInTheDocument();
    // The flat-file asset splits on '/'.
    expect(screen.getByText('raw')).toBeInTheDocument();
    expect(screen.getByText('events.parquet')).toBeInTheDocument();
  });

  it('shows an empty state when there are no assets', async () => {
    mockList.mockResolvedValue(page([]));
    renderPage();
    await waitFor(() => expect(screen.getByText(/No assets yet/)).toBeInTheDocument());
  });

  it('offers an in-place retry that refetches without a full page reload (#930)', async () => {
    mockList.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(page([ASSET]));
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }));

    // The page recovers in place — the whole point of wiring onRetry rather than
    // letting the catalog fall back to window.location.reload().
    expect(await screen.findByText('Snowflake · acct')).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(2);
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderPage();
    // #930 review: this whole-page fetch now uses AsyncBody's `page` mode, so a
    // failure renders the dedicated error page instead of a husk-of-a-page alert.
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
  });

  it('walks pages until the workspace is fully fetched (#925)', async () => {
    // The tree fetch walks in pages of 200; a 250-asset workspace takes two
    // calls (200 + 50) before it stops.
    // `gen${i}` ids: the previous `a${i}` collided with ASSET's own id 'a1'
    // at i=1 — an accidental duplicate the walk's id-dedupe (review fix) now
    // collapses, which is correct behavior but wrong fixture intent.
    const firstPage = Array.from({ length: 200 }, (_, i) =>
      i === 0
        ? ASSET
        : { ...ASSET, id: `gen${i}`, name: `ANALYTICS.PUBLIC.T${i}`, worst_severity: null },
    );
    const secondPage = Array.from({ length: 50 }, (_, i) => ({
      ...ASSET,
      id: `b${i}`,
      name: `ANALYTICS.PUBLIC.U${i}`,
      worst_severity: null,
    }));
    mockList
      .mockResolvedValueOnce({ items: firstPage, total: 250 })
      .mockResolvedValueOnce({ items: secondPage, total: 250 });
    renderPage();

    // The tree finishes only once BOTH pages have landed — the CUSTOMERS-style
    // leaf from page two proves the walk didn't stop after page one.
    expect(await screen.findByText('U49')).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledTimes(2);
    // Each call also carries the abort signal (#1107) as its second argument.
    expect(mockList).toHaveBeenNthCalledWith(1, { limit: 200, offset: 0 }, expect.any(AbortSignal));
    expect(mockList).toHaveBeenNthCalledWith(
      2,
      { limit: 200, offset: 200 },
      expect.any(AbortSignal),
    );
    // The walk covered the whole workspace (200 + 50 === 250) — no truncation note.
    expect(screen.queryByText(/Showing \d+ of \d+ assets/)).not.toBeInTheDocument();
  });

  it('collapses a row repeated across pages by a concurrent insert (review fix)', async () => {
    // Offset paging races live writes: an insert below the cursor shifts rows
    // so the last row of page one reappears at the top of page two. The walk
    // dedupes by id — the repeated asset renders once, and the pinned
    // first-page total keeps the walk's target stable.
    const repeated = {
      ...ASSET,
      id: 'dup',
      name: 'ANALYTICS.PUBLIC.REPEATED',
      worst_severity: null,
    };
    const firstPage = [
      ...Array.from({ length: 199 }, (_, i) => ({
        ...ASSET,
        id: `a${i}`,
        name: `ANALYTICS.PUBLIC.T${i}`,
        worst_severity: null,
      })),
      repeated,
    ];
    mockList.mockResolvedValueOnce({ items: firstPage, total: 201 }).mockResolvedValueOnce({
      items: [repeated, { ...ASSET, id: 'z', name: 'ANALYTICS.PUBLIC.LAST', worst_severity: null }],
      total: 202,
    });
    renderPage();

    expect(await screen.findByText('LAST')).toBeInTheDocument();
    expect(screen.getAllByText('REPEATED')).toHaveLength(1);
    // 199 unique + dup + z = 201 == pinned first-page total → complete, no note.
    expect(screen.queryByText(/Showing \d+ of \d+ assets/)).not.toBeInTheDocument();
  });

  // 20s explicit budget: ten 200-row pages + a 2000-node antd Tree render
  // sits near vitest's 5s default on CI runners (timed out there once) —
  // the size IS the point of this test, so the budget moves, not the load.
  it('renders an explicit truncation note rather than a silently partial tree (#925)', async () => {
    // The workspace has 2100 assets — past the 2000-row hard bound — so the
    // walk stops at 10 pages (2000 rows) and MUST say so, never render a tree
    // that silently dropped 100 assets.
    mockList.mockImplementation(async ({ offset } = {}) => ({
      items: Array.from({ length: 200 }, (_, i) => ({
        ...ASSET,
        id: `p${offset}-${i}`,
        name: `ANALYTICS.PUBLIC.T${offset}_${i}`,
        worst_severity: null,
      })),
      total: 2100,
    }));
    renderPage();

    expect(await screen.findByText('Showing 2000 of 2100 assets')).toBeInTheDocument();
    // Walked exactly to the bound: 2000 / 200 per page = 10 calls, not 11 — the
    // walk must stop AT the bound, not one page past it.
    expect(mockList).toHaveBeenCalledTimes(10);
  }, 20_000);

  // #1107: the multi-page tree walk used to survive a toggle-away — the loop had
  // no way to hear about the unmount, so it kept firing page requests up to
  // TREE_HARD_BOUND regardless of whether anything was still listening.
  it('stops issuing further page requests once the tree view is toggled away mid-walk (#1107)', async () => {
    const page1Items = Array.from({ length: 200 }, (_, i) => ({
      ...ASSET,
      id: `p1-${i}`,
      name: `ANALYTICS.PUBLIC.T${i}`,
      worst_severity: null,
    }));
    let resolvePage2: ((value: AssetListPage) => void) | undefined;
    const page2Pending = new Promise<AssetListPage>((resolve) => {
      resolvePage2 = resolve;
    });

    // A 600-row workspace forces (at least) a third page — offset 400 must
    // never be requested once the view is toggled away while page 2 is still
    // in flight.
    mockList.mockImplementation(async (params) => {
      if (params?.limit === 50) return { items: [], total: 0 }; // the table view's own independent fetch
      if (params?.offset === 0) return { items: page1Items, total: 600 };
      if (params?.offset === 200) return page2Pending;
      throw new Error(`unexpected listAssets call past the abort: ${JSON.stringify(params)}`);
    });

    renderPage();

    // Page 1 has resolved and the walk has already moved on to page 2 (now
    // in flight, unresolved) — "mid-walk" per the AC.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));

    // Toggle away before page 2 resolves.
    await userEvent.click(await screen.findByText('All assets'));

    // Let page 2's response land late — after the toggle-away, like a real
    // network response racing an unmount — and let the table's own (separate,
    // immediate) fetch settle so we know everything has had a chance to react.
    resolvePage2?.({ items: [], total: 600 });
    await screen.findByText(/No assets yet/);

    // Exactly the two tree-walk calls (page 1 + the in-flight page 2) plus the
    // table's own fetch — never a third tree page. If the walk had kept going
    // after the toggle, the mock above would have thrown for the offset-400 call.
    expect(mockList).toHaveBeenCalledTimes(3);
  });

  it('does not re-walk when toggling back to an already-completed tree (#1107)', async () => {
    mockList.mockResolvedValue(page([ASSET])); // total === items.length: a single-page, complete walk
    renderPage();
    await screen.findByText('Snowflake · acct');
    expect(mockList).toHaveBeenCalledTimes(1);

    await userEvent.click(await screen.findByText('All assets'));
    await screen.findByText('ANALYTICS.PUBLIC.ORDERS'); // the table's own independent fetch landed
    const callsWithTableShown = mockList.mock.calls.length;

    await userEvent.click(await screen.findByText('By source'));
    // The tree renders immediately from the cached walk, not a fresh fetch.
    await screen.findByText('Snowflake · acct');

    expect(mockList).toHaveBeenCalledTimes(callsWithTableShown);
  });

  it('never runs more than one tree walk at a time under rapid toggling (#1107)', async () => {
    const page1Items = Array.from({ length: 200 }, (_, i) => ({
      ...ASSET,
      id: `p1-${i}`,
      name: `ANALYTICS.PUBLIC.T${i}`,
      worst_severity: null,
    }));
    const page2Items = Array.from({ length: 10 }, (_, i) => ({
      ...ASSET,
      id: `p2-${i}`,
      name: `ANALYTICS.PUBLIC.U${i}`,
      worst_severity: null,
    }));
    // Every tree page-1 request is held open (a controllable pending promise)
    // so several superseded walks can all have an unresolved page-1 call in
    // flight at once — the scenario where "at most one in-flight walk" actually
    // has teeth. Page 2 (offset 200) resolves immediately once requested, and
    // only the walk that's still mounted when its page-1 finally lands may ever
    // reach it.
    const pendingPage1: Array<(value: AssetListPage) => void> = [];
    let page1Calls = 0;
    mockList.mockImplementation(async (params) => {
      if (params?.limit === 50) return { items: [], total: 0 }; // the table view's own fetch
      if (params?.offset === 0) {
        page1Calls += 1;
        return new Promise<AssetListPage>((resolve) => pendingPage1.push(resolve));
      }
      return { items: page2Items, total: 210 };
    });

    renderPage();
    await waitFor(() => expect(page1Calls).toBe(1));

    // Toggle away and back twice in quick succession — each toggle-to-tree
    // mounts a fresh AssetsTreeView (fresh useAsyncData instance) while the
    // PREVIOUS one's page-1 request is still unresolved.
    await userEvent.click(await screen.findByText('All assets'));
    await userEvent.click(await screen.findByText('By source'));
    await waitFor(() => expect(page1Calls).toBe(2));
    await userEvent.click(await screen.findByText('All assets'));
    await userEvent.click(await screen.findByText('By source'));
    await waitFor(() => expect(page1Calls).toBe(3));

    // Resolve every stalled page-1 request now, oldest (superseded) first. The
    // two superseded walks were aborted on unmount and must stay inert — never
    // requesting page 2 — leaving only the currently-mounted (3rd) walk to
    // actually finish.
    pendingPage1.forEach((resolve) => resolve({ items: page1Items, total: 210 }));

    // U9 only renders once the live walk's page 2 has landed and the tree
    // re-rendered — proof exactly one walk ran to completion.
    expect(await screen.findByText('U9')).toBeInTheDocument();
    // 3 page-1 attempts (one per mount) + exactly ONE page-2 continuation (the
    // live walk) + 2 independent table fetches (one per toggle-to-table) = 6.
    // A stale walk resuming after being superseded would push this higher.
    expect(mockList).toHaveBeenCalledTimes(6);
  });
});

describe('Assets page — table view (#925 server-side paging)', () => {
  async function switchToTable() {
    await userEvent.click(await screen.findByText('All assets'));
  }

  it('switches to the flat "All assets" table', async () => {
    mockList.mockResolvedValue(page([ASSET]));
    renderPage();
    await screen.findByText('Snowflake · acct');

    await switchToTable();
    // The table shows the full dotted name + suite count (2) the tree omits.
    expect(await screen.findByText('ANALYTICS.PUBLIC.ORDERS')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();

    // Row click still navigates from the table view.
    await userEvent.click(screen.getByText('ANALYTICS.PUBLIC.ORDERS'));
    expect(await screen.findByText('detail for asset')).toBeInTheDocument();
  });

  it('fetches page 2 at the right offset when the pager is used', async () => {
    const pageOne = Array.from({ length: 50 }, (_, i) => ({
      ...ASSET,
      id: `p1-${i}`,
      name: `ANALYTICS.PUBLIC.A${i}`,
    }));
    const pageTwo = Array.from({ length: 50 }, (_, i) => ({
      ...ASSET,
      id: `p2-${i}`,
      name: `ANALYTICS.PUBLIC.B${i}`,
    }));
    // Keyed off the real params rather than call ORDER: the tree view mounts
    // first (it's the default view) and issues its own `limit: 200` fetch
    // before the table is ever shown, so a plain `mockResolvedValueOnce` queue
    // would silently hand the tree's call the table's page-1 data.
    mockList.mockImplementation(async (params) => {
      if (params?.limit === 200) return { items: [], total: 0 }; // the tree's own walk — irrelevant here
      return params?.offset === 50
        ? { items: pageTwo, total: 120 }
        : { items: pageOne, total: 120 };
    });
    renderPage();
    await switchToTable();
    await screen.findByText('ANALYTICS.PUBLIC.A0');
    expect(mockList).toHaveBeenCalledWith({ limit: 50, offset: 0 });

    // antd renders page-number items with title="2"; go to page 2.
    await userEvent.click(screen.getByTitle('2'));

    expect(await screen.findByText('ANALYTICS.PUBLIC.B0')).toBeInTheDocument();
    expect(screen.queryByText('ANALYTICS.PUBLIC.A0')).not.toBeInTheDocument();
    expect(mockList).toHaveBeenCalledWith({ limit: 50, offset: 50 });
  });

  it('shows an empty state when there are no assets', async () => {
    mockList.mockResolvedValue(page([]));
    renderPage();
    await switchToTable();
    await waitFor(() => expect(screen.getByText(/No assets yet/)).toBeInTheDocument());
  });

  it('surfaces a load error independently of the tree view', async () => {
    mockList.mockResolvedValueOnce(page([ASSET])).mockRejectedValueOnce(new Error('boom'));
    renderPage();
    await screen.findByText('Snowflake · acct'); // tree loaded fine
    await switchToTable();
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
  });
});
