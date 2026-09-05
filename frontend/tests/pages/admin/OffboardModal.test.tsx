import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AdminUser, OffboardPreview, OffboardReceipt } from '../../../src/api/admin';
import { offboardUser, previewOffboarding } from '../../../src/api/admin';
import { searchUsers } from '../../../src/api/shares';
import { OffboardAction, OffboardModal } from '../../../src/pages/admin/OffboardModal';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  previewOffboarding: vi.fn(),
  offboardUser: vi.fn(),
}));
vi.mock('../../../src/api/shares', () => ({ searchUsers: vi.fn() }));

const mockPreview = vi.mocked(previewOffboarding);
const mockOffboard = vi.mocked(offboardUser);
const mockSearch = vi.mocked(searchUsers);

const USER: AdminUser = {
  id: 'u9',
  email: 'olivia@x.io',
  display_name: 'Olivia Leaver',
  last_seen_at: null,
  created_at: '2026-06-01T00:00:00Z',
  owned_suite_count: 0,
  shared_suite_count: 0,
  role: 'member',
  allowlist_admin: false,
};

function preview(overrides: Partial<OffboardPreview> = {}): OffboardPreview {
  return {
    user_id: 'u9',
    email: 'olivia@x.io',
    display_name: 'Olivia Leaver',
    role: 'member',
    is_self: false,
    is_last_admin: false,
    membership_state: 'member',
    membership_id: 'm1',
    membership_note: null,
    owned_suites: [],
    open_api_key_count: 2,
    live_session_count: 1,
    ...overrides,
  };
}

const RECEIPT: OffboardReceipt = {
  user_id: 'u9',
  email: 'olivia@x.io',
  new_owner_user_id: null,
  transferred_suite_ids: [],
  api_keys_revoked: 2,
  sessions_revoked: 1,
  membership_removed: false,
  skipped: [
    { step: 'transfer_suites', reason: 'this user owns no suites' },
    { step: 'remove_membership', reason: 'this address is listed in OIDC_ALLOWED_EMAILS' },
  ],
};

const render = () =>
  renderSubPage(<OffboardModal user={USER} onClose={vi.fn()} onOffboarded={vi.fn()} />);

const okButton = () => screen.getByRole('button', { name: /Offboard|Done/ });

beforeEach(() => {
  mockPreview.mockResolvedValue(preview());
  mockSearch.mockResolvedValue([]);
});
afterEach(() => vi.clearAllMocks());

describe('OffboardModal', () => {
  it('summarises what the pass would touch', async () => {
    render();
    expect(await screen.findByText('2 live')).toBeInTheDocument();
    expect(screen.getByText('1 live')).toBeInTheDocument();
    expect(screen.getByText('will be withdrawn')).toBeInTheDocument();
  });

  it('keeps the action disabled until the email is typed exactly', async () => {
    const user = userEvent.setup();
    render();
    await screen.findByText('will be withdrawn');
    expect(okButton()).toBeDisabled();

    await user.type(screen.getByLabelText('Confirm email address'), 'olivia@wrong.io');
    expect(okButton()).toBeDisabled();

    await user.clear(screen.getByLabelText('Confirm email address'));
    await user.type(screen.getByLabelText('Confirm email address'), 'olivia@x.io');
    await waitFor(() => expect(okButton()).toBeEnabled());
  });

  it('refuses the last admin outright, typed confirmation or not', async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(preview({ is_last_admin: true }));
    render();
    expect(await screen.findByText('This is the last admin in the workspace')).toBeInTheDocument();

    // The input is disabled, so the action can never become available.
    expect(screen.getByLabelText('Confirm email address')).toBeDisabled();
    await user.click(okButton());
    expect(mockOffboard).not.toHaveBeenCalled();
  });

  it('needs a new owner before it will run when the user owns suites', async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(
      preview({
        owned_suites: [
          { id: 's1', name: 'Finance DQ', check_count: 7, run_count: 3, result_count: 21 },
        ],
      }),
    );
    render();
    expect(await screen.findByText('Finance DQ')).toBeInTheDocument();
    expect(screen.getByText('7 check(s) · 3 run(s)')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Confirm email address'), 'olivia@x.io');
    // Typed correctly and still disabled: the suites have nowhere to go.
    expect(okButton()).toBeDisabled();
  });

  it('names the env variable when membership cannot be withdrawn here', async () => {
    mockPreview.mockResolvedValue(
      preview({
        membership_state: 'env_listed',
        membership_note: 'this address is listed in OIDC_ALLOWED_EMAILS',
      }),
    );
    render();
    expect(await screen.findByText('env-listed')).toBeInTheDocument();
    expect(screen.getByText(/this address is listed in OIDC_ALLOWED_EMAILS/)).toBeInTheDocument();
  });

  it('shows a receipt saying what ran and what did not', async () => {
    const user = userEvent.setup();
    mockOffboard.mockResolvedValue(RECEIPT);
    render();
    await screen.findByText('will be withdrawn');
    await user.type(screen.getByLabelText('Confirm email address'), 'olivia@x.io');
    await waitFor(() => expect(okButton()).toBeEnabled());
    await user.click(okButton());

    // Both the receipt banner and the toast say it, so match all of them.
    expect((await screen.findAllByText('olivia@x.io has been offboarded')).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText('Not done, and why')).toBeInTheDocument();
    expect(screen.getByText(/this user owns no suites/)).toBeInTheDocument();
    // The receipt must not let "membership withdrawn" read as done when it wasn't.
    expect(screen.getByText('no')).toBeInTheDocument();
    expect(mockOffboard).toHaveBeenCalledWith('u9', {
      new_owner_user_id: null,
      keep_previous_owner_access: false,
      confirm_email: 'olivia@x.io',
    });
  });

  it('sends the picked owner and never offers one the backend would reject', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mockPreview.mockResolvedValue(
      preview({
        owned_suites: [
          { id: 's1', name: 'Finance DQ', check_count: 7, run_count: 3, result_count: 21 },
        ],
      }),
    );
    mockSearch.mockResolvedValue([
      { id: 'heir', email: 'heir@x.io', display_name: null, role: 'member' },
      { id: 'vic', email: 'vic@x.io', display_name: null, role: 'viewer' },
      // The leaver: the backend refuses them inheriting their own suites.
      { id: 'u9', email: 'olivia@x.io', display_name: null, role: 'member' },
    ]);
    mockOffboard.mockResolvedValue({ ...RECEIPT, new_owner_user_id: 'heir' });
    render();
    await screen.findByText('Finance DQ');

    await user.type(screen.getByLabelText('Confirm email address'), 'olivia@x.io');
    await user.click(screen.getByLabelText('New owner'));
    await user.type(screen.getByLabelText('New owner'), 'x.io');
    await vi.advanceTimersByTimeAsync(400);

    expect(await screen.findByTitle('heir@x.io')).toBeInTheDocument();
    // A viewer can't own a suite, and neither can the departing user.
    expect(screen.queryByTitle('vic@x.io')).not.toBeInTheDocument();
    expect(screen.queryByTitle('olivia@x.io')).not.toBeInTheDocument();

    await user.click(screen.getByTitle('heir@x.io'));
    await waitFor(() => expect(okButton()).toBeEnabled());
    await user.click(okButton());

    await waitFor(() =>
      expect(mockOffboard).toHaveBeenCalledWith('u9', {
        new_owner_user_id: 'heir',
        keep_previous_owner_access: false,
        confirm_email: 'olivia@x.io',
      }),
    );
    vi.useRealTimers();
  });

  it('opens from the members-table row action', async () => {
    const user = userEvent.setup();
    renderSubPage(<OffboardAction user={USER} onOffboarded={vi.fn()} />);
    expect(mockPreview).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Offboard' }));

    expect(await screen.findByText('will be withdrawn')).toBeInTheDocument();
    expect(mockPreview).toHaveBeenCalledWith('u9', expect.anything());
  });

  it('surfaces a preview failure instead of an empty confirmation form', async () => {
    mockPreview.mockImplementationOnce(() => Promise.reject(new Error('boom')));
    render();
    expect(await screen.findByText('Could not load the preview')).toBeInTheDocument();
    expect(screen.queryByLabelText('Confirm email address')).not.toBeInTheDocument();
  });
});
