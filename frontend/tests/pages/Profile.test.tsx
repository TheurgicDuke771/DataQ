import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { listSuites, type Suite } from '../../src/api/suites';
import { authMethodLabel } from '../../src/auth/config';
import { MeContext } from '../../src/auth/meContext';
import { useSaveDisplayName } from '../../src/auth/useSaveDisplayName';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Profile } from '../../src/pages/Profile';

// The ApiKeysPanel on the profile fetches the user's PATs on mount; stub the client so the page
// tests don't hit the network (its own behaviour is covered in ApiKeysPanel.test.tsx).
vi.mock('../../src/api/apiKeys', () => ({
  listApiKeys: vi.fn().mockResolvedValue([]),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  PAT_DEFAULT_EXPIRY_DAYS: 90,
  PAT_MAX_EXPIRY_DAYS: 365,
}));

// The access summary + owned-suites list read the caller-scoped suite list.
vi.mock('../../src/api/suites', () => ({ listSuites: vi.fn() }));
const mockListSuites = vi.mocked(listSuites);

function suite(id: string, name: string, my_permission: Suite['my_permission']): Suite {
  return {
    id,
    name,
    description: id === 's-owned-2' ? 'Nightly orders checks' : null,
    connection_id: 'c-1',
    target: null,
    created_by: 'u-1',
    my_permission,
  };
}

const SUITES: Suite[] = [
  suite('s-owned-2', 'Zulu orders', 'owner'),
  suite('s-owned-1', 'Alpha customers', 'owner'),
  suite('s-edit', 'Shared edit', 'edit'),
  suite('s-view-1', 'Shared view 1', 'view'),
  suite('s-view-2', 'Shared view 2', 'view'),
];

// The name-edit affordance (#1139) goes through the shared save hook; stubbed here so this file can
// assert the interaction without a real PATCH.
const saveDisplayName = vi.fn();
vi.mock('../../src/auth/useSaveDisplayName', () => ({ useSaveDisplayName: vi.fn() }));

const me: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'ada@dataq.io',
    display_name: 'Ada Lovelace',
    last_seen_at: '2026-06-26T10:00:00Z',
    role: 'member' as const,
    is_workspace_admin: false,
  },
};

beforeEach(() => {
  saveDisplayName.mockReset();
  vi.mocked(useSaveDisplayName).mockReturnValue(saveDisplayName);
  mockListSuites.mockReset();
  mockListSuites.mockResolvedValue(SUITES);
});

function renderProfile(state: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={state}>
          <Profile />
        </MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

describe('Profile', () => {
  it('renders identity + workspace facts from /me', () => {
    renderProfile(me);
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('ada@dataq.io')).toBeInTheDocument();
    // The auth label derives from the runtime authMode (never a hardcoded
    // provider/library name — ADR 0028; per-mode wording pinned in config.test.ts).
    expect(screen.getByText(authMethodLabel)).toBeInTheDocument();
    expect(screen.getByText('2026-06-26T10:00:00Z')).toBeInTheDocument();
    // Member, not admin.
    expect(screen.getAllByText('Member').length).toBeGreaterThan(0);
  });

  it('tags a workspace admin from the effective role the server resolved', () => {
    // `role` is the effective role (a break-glass allowlist admin reads `admin`); the page must
    // not re-derive it from `is_workspace_admin`.
    renderProfile({ ...me, data: { ...me.data, role: 'admin', is_workspace_admin: true } });
    expect(screen.getAllByText('Workspace admin').length).toBeGreaterThan(0);
  });

  it('labels a viewer as read-only, never as a member', () => {
    renderProfile({ ...me, data: { ...me.data, role: 'viewer' } });
    expect(screen.getAllByText('Viewer').length).toBeGreaterThan(0);
    expect(screen.queryByText('Member')).toBeNull();
    expect(
      screen.getByText(/Read-only: you can open the suites shared with you/),
    ).toBeInTheDocument();
  });

  describe('your access and your suites', () => {
    it('lists the suites the caller owns, alphabetically, as links to each suite', async () => {
      renderProfile(me);
      const list = await screen.findByRole('list', { name: 'Suites you own' });
      const links = within(list).getAllByRole('link');
      expect(links.map((l) => l.textContent)).toEqual(['Alpha customers', 'Zulu orders']);
      expect(links[1]).toHaveAttribute('href', '/suites/s-owned-2');
      expect(screen.getByText('Nightly orders checks')).toBeInTheDocument();
      // Shared suites are not "yours".
      expect(within(list).queryByText('Shared edit')).toBeNull();
    });

    it("counts the caller's suites by access level", async () => {
      renderProfile(me);
      await screen.findByRole('list', { name: 'Suites you own' });
      const summary = screen.getByLabelText('Suites by access level');
      const row = (label: string) =>
        within(summary).getByText(label).closest('.ant-descriptions-item')?.textContent;
      expect(row('Owned by you')).toMatch(/2$/);
      expect(row('Shared with you as admin')).toMatch(/0$/);
      expect(row('Shared with you to edit')).toMatch(/1$/);
      expect(row('Shared with you to view')).toMatch(/2$/);
    });

    it('explains an empty owned list instead of showing nothing', async () => {
      mockListSuites.mockResolvedValue(SUITES.filter((s) => s.my_permission !== 'owner'));
      renderProfile(me);
      expect(await screen.findByText(/You don't own any suites/)).toBeInTheDocument();
      expect(screen.queryByRole('list', { name: 'Suites you own' })).toBeNull();
    });

    it('never shows an admin per-share counts, since every suite reads as admin for them', async () => {
      // GET /suites stamps every non-owned suite `admin` for a workspace admin (ADR 0033), so a
      // "Shared with you as admin" row would count suites nobody shared.
      mockListSuites.mockResolvedValue(
        SUITES.map((s) => (s.my_permission === 'owner' ? s : { ...s, my_permission: 'admin' })),
      );
      renderProfile({ ...me, data: { ...me.data, role: 'admin', is_workspace_admin: true } });
      const summary = await screen.findByLabelText('Suites by access level');
      const row = (label: string) =>
        within(summary).getByText(label).closest('.ant-descriptions-item')?.textContent;
      expect(row('Owned by you')).toMatch(/2$/);
      expect(row('All other suites in the workspace')).toMatch(/3$/);
      expect(within(summary).queryByText('Shared with you as admin')).toBeNull();
      expect(
        screen.getByText(/you can act on every suite whether or not it was shared with you/),
      ).toBeInTheDocument();
    });

    it('does not fetch suites until /me has resolved', () => {
      renderProfile({ status: 'loading' });
      expect(mockListSuites).not.toHaveBeenCalled();
    });

    it('shows the failure with a retry when the suite list cannot load', async () => {
      mockListSuites.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(SUITES);
      renderProfile(me);
      expect(await screen.findByText('Could not load your suites')).toBeInTheDocument();
      expect(screen.getByText('Could not load your suite access')).toBeInTheDocument();
      // The identity card and API keys are unaffected by a suites outage.
      expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    });
  });

  it('shows an error state when /me fails', () => {
    renderProfile({ status: 'error', error: 'boom', kind: 'http' as const });
    // #910: dedicated error page (no status on the stubbed state → 500).
    expect(screen.getByText('500 — Something went wrong')).toBeInTheDocument();
  });

  // ── display-name edit affordance (#1139) — every mode, not just otp ────────

  describe('editing the display name', () => {
    // antd's Typography editable textarea confirms on blur as well as Enter (`Editable`'s onBlur →
    // confirmChange, unconditionally) — blur is the reliable one to drive from a test.
    const blurTheTextbox = () => userEvent.tab();

    it('saves the new name through the shared hook', async () => {
      saveDisplayName.mockResolvedValue(undefined);
      renderProfile(me);

      await userEvent.click(screen.getByRole('button', { name: 'Edit your display name' }));
      const box = screen.getByRole('textbox');
      await userEvent.clear(box);
      await userEvent.type(box, 'New Name');
      await blurTheTextbox();

      await waitFor(() => expect(saveDisplayName).toHaveBeenCalledWith('New Name'));
    });

    it('does not save when the value is unchanged', async () => {
      renderProfile(me);

      await userEvent.click(screen.getByRole('button', { name: 'Edit your display name' }));
      screen.getByRole('textbox');
      await blurTheTextbox(); // blur with no edits — antd still fires onChange('Ada Lovelace')

      expect(saveDisplayName).not.toHaveBeenCalled();
    });

    it('surfaces a failure instead of losing it silently', async () => {
      saveDisplayName.mockRejectedValue(new Error('network blew up'));
      renderProfile(me);

      await userEvent.click(screen.getByRole('button', { name: 'Edit your display name' }));
      const box = screen.getByRole('textbox');
      await userEvent.clear(box);
      await userEvent.type(box, 'New Name');
      await blurTheTextbox();

      expect(await screen.findByText(/Could not update your name/)).toBeInTheDocument();
    });

    it('offers the same edit affordance when there is no name yet (otp, pre-#1139-prompt)', () => {
      renderProfile({ ...me, data: { ...me.data, display_name: null } });
      // Falls back to the email as the displayed label, but is still editable.
      expect(screen.getByRole('button', { name: 'Edit your display name' })).toBeInTheDocument();
    });

    it('does not PATCH the email in as the name on a bare blur (null display_name)', async () => {
      // Regression: for a null-display_name user, the rendered/editable value is the EMAIL (the
      // `name = display_name ?? email` fallback).
      renderProfile({ ...me, data: { ...me.data, display_name: null } });

      await userEvent.click(screen.getByRole('button', { name: 'Edit your display name' }));
      screen.getByRole('textbox'); // showing "ada@dataq.io" — untouched
      await blurTheTextbox();

      expect(saveDisplayName).not.toHaveBeenCalled();
    });
  });
});
