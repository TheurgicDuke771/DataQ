import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  deleteAdminSuite,
  listAdminSuites,
  revokeAdminGrant,
  transferAdminSuite,
  type AdminAccess,
} from '../../../src/api/admin';
import { searchUsers } from '../../../src/api/shares';
import { getSuiteDeletionImpact } from '../../../src/api/suites';
import { AccessGrantActions } from '../../../src/pages/admin/AccessGrantActions';
import { AdminSuites } from '../../../src/pages/admin/AdminSuites';
import { SuiteAdminDeleteModal } from '../../../src/pages/admin/SuiteAdminDeleteModal';
import { SuiteTransferModal } from '../../../src/pages/admin/SuiteTransferModal';
import { ACCESS, SUITE, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAdminSuites: vi.fn(),
  revokeAdminGrant: vi.fn(),
  transferAdminSuite: vi.fn(),
  deleteAdminSuite: vi.fn(),
}));
vi.mock('../../../src/api/shares', () => ({ searchUsers: vi.fn() }));
vi.mock('../../../src/api/suites', () => ({ getSuiteDeletionImpact: vi.fn() }));

const mockRevoke = vi.mocked(revokeAdminGrant);
const mockTransfer = vi.mocked(transferAdminSuite);
const mockDelete = vi.mocked(deleteAdminSuite);
const mockSearch = vi.mocked(searchUsers);
const mockImpact = vi.mocked(getSuiteDeletionImpact);
const mockSuites = vi.mocked(listAdminSuites);

const OWNER_ROW = ACCESS[0];
const SHARE_ROW: AdminAccess = ACCESS[1];

afterEach(() => vi.clearAllMocks());

describe('AccessGrantActions', () => {
  it('revokes a share only after the confirm is accepted', async () => {
    const user = userEvent.setup();
    mockRevoke.mockResolvedValue(undefined);
    const onRevoked = vi.fn();
    renderSubPage(<AccessGrantActions grant={SHARE_ROW} onRevoked={onRevoked} />);

    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    // The click only opens the confirm — nothing is sent yet.
    expect(mockRevoke).not.toHaveBeenCalled();
    expect(await screen.findByText(/loses edit access to Finance DQ/)).toBeInTheDocument();
    // The popconfirm's own OK button carries the same label; it is the later one.
    const buttons = screen.getAllByRole('button', { name: 'Revoke' });
    await user.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('s1', 'g1'));
    await waitFor(() => expect(onRevoked).toHaveBeenCalled());
  });

  it('offers nothing to revoke on an owner row', async () => {
    renderSubPage(<AccessGrantActions grant={OWNER_ROW} onRevoked={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeDisabled();
  });
});

describe('SuiteTransferModal', () => {
  beforeEach(() =>
    mockSearch.mockResolvedValue([
      { id: 'm1', email: 'mia@x.io', display_name: 'Mia Member', role: 'member' },
      { id: 'v1', email: 'vic@x.io', display_name: 'Vic Viewer', role: 'viewer' },
      { id: 'o1', email: 'olive@x.io', display_name: 'Olive Owner', role: 'member' },
    ]),
  );

  it('excludes workspace viewers and the current owner from the picker', async () => {
    const user = userEvent.setup();
    renderSubPage(<SuiteTransferModal suite={SUITE} onClose={vi.fn()} onTransferred={vi.fn()} />);

    await user.click(screen.getByRole('combobox'));
    await user.type(screen.getByRole('combobox'), 'x.io');

    expect(await screen.findByText('Mia Member · mia@x.io')).toBeInTheDocument();
    // A viewer cannot own a suite (ADR 0033), and the current owner already does.
    expect(screen.queryByText('Vic Viewer · vic@x.io')).not.toBeInTheDocument();
    expect(screen.queryByText('Olive Owner · olive@x.io')).not.toBeInTheDocument();
  });

  it('transfers to the picked user, keeping the previous owner an editor by default', async () => {
    const user = userEvent.setup();
    mockTransfer.mockResolvedValue({
      suite_id: 's1',
      previous_owner_id: 'o1',
      new_owner_id: 'm1',
      previous_owner_permission: 'edit',
    });
    const onTransferred = vi.fn();
    renderSubPage(
      <SuiteTransferModal suite={SUITE} onClose={vi.fn()} onTransferred={onTransferred} />,
    );

    await user.click(screen.getByRole('combobox'));
    await user.type(screen.getByRole('combobox'), 'mia');
    await user.click(await screen.findByText('Mia Member · mia@x.io'));
    await user.click(screen.getByRole('button', { name: 'Transfer' }));

    await waitFor(() =>
      expect(mockTransfer).toHaveBeenCalledWith('s1', {
        new_owner_user_id: 'm1',
        keep_previous_owner_access: true,
      }),
    );
    await waitFor(() => expect(onTransferred).toHaveBeenCalled());
  });

  it('cannot be submitted before a new owner is picked', () => {
    renderSubPage(<SuiteTransferModal suite={SUITE} onClose={vi.fn()} onTransferred={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Transfer' })).toBeDisabled();
  });
});

describe('SuiteAdminDeleteModal', () => {
  beforeEach(() =>
    mockImpact.mockResolvedValue({
      checks: 7,
      runs: 12,
      results: 84,
      trigger_bindings: 0,
      schedules: 1,
    }),
  );

  it('states the blast radius and holds the delete until the name is typed', async () => {
    const user = userEvent.setup();
    mockDelete.mockResolvedValue(undefined);
    const onDeleted = vi.fn();
    renderSubPage(<SuiteAdminDeleteModal suite={SUITE} onClose={vi.fn()} onDeleted={onDeleted} />);

    expect(await screen.findByText(/Deletes 7 checks, 12 runs and 84 results/)).toBeInTheDocument();
    expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();

    const confirm = screen.getByRole('button', { name: 'Delete' });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText('Suite name confirmation'), 'Finance');
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText('Suite name confirmation'), ' DQ');
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('s1'));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it('still allows the delete when the counts cannot be fetched', async () => {
    mockImpact.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<SuiteAdminDeleteModal suite={SUITE} onClose={vi.fn()} onDeleted={vi.fn()} />);
    expect(await screen.findByText(/Counts unavailable/)).toBeInTheDocument();
  });
});

describe('AdminSuites actions', () => {
  beforeEach(() => {
    mockSuites.mockResolvedValue([SUITE]);
    mockSearch.mockResolvedValue([]);
    mockImpact.mockResolvedValue({
      checks: 0,
      runs: 0,
      results: 0,
      trigger_bindings: 0,
      schedules: 0,
    });
  });

  it('opens the transfer modal for the row it was clicked on', async () => {
    const user = userEvent.setup();
    renderSubPage(<AdminSuites />);
    await user.click(await screen.findByRole('button', { name: 'Transfer' }));
    expect(await screen.findByText('Transfer “Finance DQ”')).toBeInTheDocument();
  });

  it('opens the delete modal for the row it was clicked on', async () => {
    const user = userEvent.setup();
    renderSubPage(<AdminSuites />);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Delete “Finance DQ”?')).toBeInTheDocument();
  });
});
