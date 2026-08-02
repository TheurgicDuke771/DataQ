import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { authMethodLabel } from '../../src/auth/config';
import { MeContext } from '../../src/auth/meContext';
import { useSaveDisplayName } from '../../src/auth/useSaveDisplayName';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Profile } from '../../src/pages/Profile';

// The ApiKeysPanel on the profile fetches the user's PATs on mount; stub the
// client so the page tests don't hit the network (its own behaviour is covered
// in ApiKeysPanel.test.tsx).
vi.mock('../../src/api/apiKeys', () => ({
  listApiKeys: vi.fn().mockResolvedValue([]),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  PAT_DEFAULT_EXPIRY_DAYS: 90,
  PAT_MAX_EXPIRY_DAYS: 365,
}));

// The name-edit affordance (#1139) goes through the shared save hook; stubbed
// here so this file can assert the interaction without a real PATCH — the
// hook's own PATCH→MeContext→otp-session fan-out is covered in
// useSaveDisplayName.test.tsx.
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
    is_workspace_admin: false,
  },
};

beforeEach(() => {
  saveDisplayName.mockReset();
  vi.mocked(useSaveDisplayName).mockReturnValue(saveDisplayName);
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

  it('tags a workspace admin', () => {
    renderProfile({ ...me, data: { ...me.data, is_workspace_admin: true } });
    expect(screen.getAllByText('Workspace admin').length).toBeGreaterThan(0);
  });

  it('points alerting config at suites (per-suite, not per-user)', () => {
    renderProfile(me);
    expect(screen.getByText('DQ alerts are configured per suite')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Suites' })).toHaveAttribute('href', '/suites');
  });

  it('shows an error state when /me fails', () => {
    renderProfile({ status: 'error', error: 'boom', kind: 'http' as const });
    // #910: dedicated error page (no status on the stubbed state → 500).
    expect(screen.getByText('500 — Something went wrong')).toBeInTheDocument();
  });

  // ── display-name edit affordance (#1139) — every mode, not just otp ────────

  describe('editing the display name', () => {
    // antd's Typography editable textarea confirms on blur as well as Enter
    // (`Editable`'s onBlur → confirmChange, unconditionally) — blur is the
    // reliable one to drive from a test, since userEvent's synthetic Enter
    // keyCode doesn't reach rc-component's own keyCode-compared handler in
    // jsdom. Real browsers get both paths; this only exercises one of them.
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
  });
});
