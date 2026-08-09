import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Connection,
  deleteConnection,
  listConnections,
  reauthConnection,
  testConnection,
} from '../../src/api/connections';
import { Connections } from '../../src/pages/Connections';

// Keep the real CONNECTION_TYPES / labels; mock only the network functions.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return {
    ...actual,
    listConnections: vi.fn(),
    testConnection: vi.fn(),
    deleteConnection: vi.fn(),
    reauthConnection: vi.fn(),
  };
});

const mockList = vi.mocked(listConnections);
const mockTest = vi.mocked(testConnection);
const mockDelete = vi.mocked(deleteConnection);
const mockReauth = vi.mocked(reauthConnection);

function conn(overrides: Partial<Connection>): Connection {
  return {
    id: 'c1',
    name: 'sf-dev',
    type: 'snowflake',
    env: 'dev',
    config: {},
    has_secret: true,
    created_by: 'u1',
    ...overrides,
  };
}

// ConnectionCard uses antd's App.useApp() for messages → wrap in <AntApp>; the
// page navigates to /connections/new → wrap in a router.
function renderPage() {
  return render(
    <MemoryRouter>
      <AntApp>
        <Connections />
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Connections', () => {
  it('groups connections by type with env + credential badges', async () => {
    mockList.mockResolvedValue([
      conn({ id: 'c1', name: 'sf-dev', type: 'snowflake', env: 'dev', has_secret: true }),
      conn({ id: 'c2', name: 's3-prod', type: 's3', env: 'prod', has_secret: false }),
    ]);

    renderPage();

    expect(await screen.findByText('sf-dev')).toBeInTheDocument();
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('AWS S3')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    expect(screen.getByText('PROD')).toBeInTheDocument();
    expect(screen.getByText('credential set')).toBeInTheDocument();
    expect(screen.getByText('no credential')).toBeInTheDocument();
  });

  it('flags a datasource whose recent runs are all failing, without clicking Test (#954)', async () => {
    // The whole point: a dead credential must be visible on the LIST. Two prod
    // Snowflake connections sat dead for weeks because this badge did not exist —
    // the failure showed on the run, not on the connection that caused it.
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'sf-orders',
        consecutive_run_failures: 4,
        last_run_error: 'The datasource rejected the credentials.',
      }),
      conn({ id: 'c2', name: 'sf-healthy' }),
    ]);

    renderPage();

    expect(await screen.findByText('runs failing (4×)')).toBeInTheDocument();
    // …and a healthy connection stays unbadged, so the signal means something.
    expect(screen.queryByText(/runs failing \(0/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/runs failing/)).toHaveLength(1);
  });

  it('warns before a credential expires, and stays silent when it cannot know (#838)', async () => {
    // The half #828 left undone: an ADLS SAS states its own expiry, so the
    // product can say so BEFORE lineage goes dark for six days. Equally
    // important is the third card — a credential with no readable lifetime gets
    // no badge at all, because a reassuring badge would be worse than none.
    const inDays = (d: number) => new Date(Date.now() + d * 86_400_000).toISOString();
    mockList.mockResolvedValue([
      conn({ id: 'c1', name: 'adls-soon', credential_expires_at: inDays(5) }),
      conn({ id: 'c2', name: 'adls-dead', credential_expires_at: inDays(-1) }),
      conn({ id: 'c3', name: 'sf-no-expiry', credential_expires_at: null }),
      conn({ id: 'c4', name: 'adls-fine', credential_expires_at: inDays(120) }),
    ]);

    renderPage();

    expect(await screen.findByText('credential expires in 5d')).toBeInTheDocument();
    expect(screen.getByText('credential expired')).toBeInTheDocument();
    // Exactly two badges: the unknown and the far-off credentials are unbadged,
    // so the signal keeps meaning something.
    expect(screen.getAllByText(/credential expire/)).toHaveLength(2);
  });

  it('says "expiry unknown" when the credential has never been checked (#1024)', async () => {
    // Saying nothing for both "no expiry" and "not looked yet" is what made an
    // unchecked credential look safe: the absence of a warning read as
    // reassurance. Prod showed every connection NULL after a deploy, including
    // SAS-bearing ones whose expiry is printed in the token.
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'never-checked' })]);

    renderPage();

    expect(await screen.findByText('expiry unknown')).toBeInTheDocument();
  });

  it('flags a connection whose inventory sync is failing, only when opted in (#1104)', async () => {
    // A connection opted into inventory sync whose principal can't read the
    // enumeration query used to fail every daily tick invisibly: toggle on,
    // connection test green, zero assets, no surface said why (#828 shape).
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'uc-catalog',
        type: 'unity_catalog',
        config: { inventory_sync: true },
        inventory_sync_failing_since: '2026-08-01T00:00:00Z',
        inventory_sync_last_error:
          'Inventory sync is missing a SELECT grant on `system.information_schema`.',
      }),
      // Opted in but currently healthy — no badge.
      conn({
        id: 'c2',
        name: 'uc-healthy',
        type: 'unity_catalog',
        config: { inventory_sync: true },
      }),
      // NOT opted in, even though failing_since happens to be set — must stay
      // unbadged, since the badge is scoped to opted-in connections only.
      conn({
        id: 'c3',
        name: 'uc-not-opted-in',
        type: 'unity_catalog',
        inventory_sync_failing_since: '2026-08-01T00:00:00Z',
      }),
    ]);

    renderPage();

    await screen.findByText('uc-catalog');
    expect(screen.getAllByText('inventory sync failing')).toHaveLength(1);
  });

  it('shows a neutral note for a database that has always enumerated zero tables (#1242)', async () => {
    // Snowflake's INFORMATION_SCHEMA is privilege-filtered, not access-denied, so
    // a role with no grants "succeeds" at zero rows — indistinguishable from a
    // genuinely empty database. This must read as informational, not an error.
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'sf-empty-db',
        config: { inventory_sync: true },
        inventory_sync_last_table_count: 0,
        inventory_sync_zero_since: null,
      }),
    ]);

    renderPage();

    expect(await screen.findByText('0 tables found')).toBeInTheDocument();
    expect(screen.queryByText('tables dropped to 0')).not.toBeInTheDocument();
  });

  it('flags a drop from N>0 to 0 tables distinctly from the neutral zero state (#1242)', async () => {
    // The privilege-loss/dropped-database signal: a connection that USED TO see
    // tables and now sees none is worth flagging, unlike one that always did.
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'sf-dropped',
        config: { inventory_sync: true },
        inventory_sync_last_table_count: 0,
        inventory_sync_zero_since: '2026-08-01T00:00:00Z',
      }),
      // Currently N>0 — neither badge shows.
      conn({
        id: 'c2',
        name: 'sf-healthy-count',
        config: { inventory_sync: true },
        inventory_sync_last_table_count: 12,
      }),
    ]);

    renderPage();

    expect(await screen.findByText('tables dropped to 0')).toBeInTheDocument();
    expect(screen.queryByText('0 tables found')).not.toBeInTheDocument();
    expect(screen.getAllByText('tables dropped to 0')).toHaveLength(1);
  });

  it('never shows a zero-table badge for a connection currently failing to sync (#1242)', async () => {
    // A stale `inventory_sync_last_table_count` from before the sync started
    // erroring must not render as if it were the current state — the failing
    // badge above already covers "something is wrong here".
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'uc-failing-with-stale-count',
        type: 'unity_catalog',
        config: { inventory_sync: true },
        inventory_sync_failing_since: '2026-08-01T00:00:00Z',
        inventory_sync_last_error: 'Inventory sync is missing a SELECT grant.',
        inventory_sync_last_table_count: 0,
      }),
    ]);

    renderPage();

    expect(await screen.findByText('inventory sync failing')).toBeInTheDocument();
    expect(screen.queryByText('0 tables found')).not.toBeInTheDocument();
    expect(screen.queryByText('tables dropped to 0')).not.toBeInTheDocument();
  });

  it('stays silent once checked and the credential states no expiry', async () => {
    // A Snowflake PAT or S3 key genuinely has no readable lifetime. Having looked,
    // silence is the correct and permanent answer — not a nag we cannot resolve.
    mockList.mockResolvedValue([
      conn({
        id: 'c1',
        name: 'checked-no-expiry',
        credential_expiry_checked_at: '2026-07-26T00:00:00Z',
      }),
    ]);

    renderPage();

    await screen.findByText('checked-no-expiry');
    expect(screen.queryByText('expiry unknown')).not.toBeInTheDocument();
  });

  it('shows an empty state when there are no connections', async () => {
    mockList.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('No connections configured yet')).toBeInTheDocument();
  });

  it('runs a connectivity test from a card and shows a healthy badge', async () => {
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockTest.mockResolvedValue({ ok: true });

    renderPage();
    await screen.findByText('sf-dev');
    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    expect(mockTest).toHaveBeenCalledWith('c1');
    expect(await screen.findByText('healthy')).toBeInTheDocument();
  });

  it('bulk-tests every connection via "Test all" and flags failures with a re-auth link', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([
      conn({ id: 'c1', name: 'sf-dev' }),
      conn({ id: 'c2', name: 's3-prod', type: 's3' }),
    ]);
    // c1 reachable, c2 unreachable.
    mockTest.mockImplementation((id: string) => Promise.resolve({ ok: id === 'c1' }));

    renderPage();
    await screen.findByText('sf-dev');
    await user.click(screen.getByRole('button', { name: 'Test all' }));

    // Both tested; one healthy, one unreachable + a re-auth affordance.
    await waitFor(() => expect(mockTest).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('healthy')).toBeInTheDocument();
    expect(await screen.findByText('unreachable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-authenticate' })).toBeInTheDocument();
  });

  it('clears the unreachable badge after a successful re-authentication', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockTest.mockResolvedValue({ ok: false });
    mockReauth.mockResolvedValue({ ok: true });

    renderPage();
    await screen.findByText('sf-dev');

    // Fail a test → unreachable badge + inline re-auth link.
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('unreachable')).toBeInTheDocument();

    // Re-auth via the inline link, rotate the credential successfully.
    await user.click(screen.getByRole('button', { name: 'Re-authenticate' }));
    await user.type(await screen.findByLabelText('New: Password'), 'fresh-secret');
    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    // The stale verdict is dropped — badge + link gone until re-tested.
    await waitFor(() => expect(mockReauth).toHaveBeenCalledWith('c1', 'fresh-secret'));
    await waitFor(() => expect(screen.queryByText('unreachable')).not.toBeInTheDocument());
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));

    renderPage();

    // #910: dedicated error page, not the old inline alert. A plain Error is a
    // CLIENT failure → 500; only a real network failure claims 503 (#930 review).
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('deletes a connection via the actions menu after confirming', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockDelete.mockResolvedValue();

    renderPage();
    await screen.findByText('sf-dev');

    // Open the card's actions menu and choose Delete.
    await user.click(screen.getByRole('button', { name: 'sf-dev actions' }));
    await user.click(await screen.findByText('Delete'));

    // Confirm in the modal (its OK button is also labelled "Delete").
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('c1'));
  });
});
