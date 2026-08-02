import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { useMe } from '../../src/auth/useMe';
import { useSaveDisplayName } from '../../src/auth/useSaveDisplayName';
import { ProfileCompletionPrompt } from '../../src/components/profile/ProfileCompletionPrompt';
import type { AsyncState } from '../../src/hooks/useAsyncData';

// authMode is bound at module load (ADR 0028); this file pins it to 'otp' —
// the only mode the prompt ever shows in. Non-otp coverage lives in its own
// file below (a hoisted vi.mock can't vary per-test in one file — same reason
// App.otpSignOut.test.tsx is separate from App.test.tsx).
vi.mock('../../src/auth/config', () => ({ authMode: 'otp' }));
vi.mock('../../src/auth/useMe', () => ({ useMe: vi.fn() }));
vi.mock('../../src/auth/useSaveDisplayName', () => ({ useSaveDisplayName: vi.fn() }));

const otpUserNoName: MeResponse = {
  id: 'u-1',
  aad_object_id: null,
  email: 'new@dataq.io',
  display_name: null,
  last_seen_at: null,
  is_workspace_admin: false,
};

function ok(data: MeResponse): AsyncState<MeResponse> {
  return { status: 'ok', data };
}

const save = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  save.mockReset();
  vi.mocked(useSaveDisplayName).mockReturnValue(save);
});

function renderPrompt() {
  return render(
    <AntApp>
      <ProfileCompletionPrompt />
    </AntApp>,
  );
}

describe('ProfileCompletionPrompt', () => {
  it('shows once /me resolves with a null display_name', () => {
    vi.mocked(useMe).mockReturnValue(ok(otpUserNoName));
    renderPrompt();
    expect(screen.getByText('Welcome to DataQ')).toBeInTheDocument();
  });

  it('does not show once a display name is already set', () => {
    vi.mocked(useMe).mockReturnValue(ok({ ...otpUserNoName, display_name: 'Ada' }));
    renderPrompt();
    expect(screen.queryByText('Welcome to DataQ')).not.toBeInTheDocument();
  });

  it('does not show while /me is still loading', () => {
    vi.mocked(useMe).mockReturnValue({ status: 'loading' });
    renderPrompt();
    expect(screen.queryByText('Welcome to DataQ')).not.toBeInTheDocument();
  });

  it('Save starts disabled until something is typed', () => {
    vi.mocked(useMe).mockReturnValue(ok(otpUserNoName));
    renderPrompt();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('save calls the shared save hook with the trimmed name', async () => {
    vi.mocked(useMe).mockReturnValue(ok(otpUserNoName));
    save.mockResolvedValue(undefined);
    renderPrompt();

    await userEvent.type(screen.getByLabelText('Display name'), '  Olivia Rivera  ');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save).toHaveBeenCalledWith('Olivia Rivera'));
  });

  it('a failed save shows the error and leaves the prompt open', async () => {
    vi.mocked(useMe).mockReturnValue(ok(otpUserNoName));
    save.mockRejectedValue(new Error('network blew up'));
    renderPrompt();

    await userEvent.type(screen.getByLabelText('Display name'), 'X');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.getByText('network blew up')).toBeInTheDocument());
    expect(screen.getByText('Welcome to DataQ')).toBeInTheDocument();
  });

  it('skip dismisses it and it does not re-nag for the rest of the session', async () => {
    vi.mocked(useMe).mockReturnValue(ok(otpUserNoName));
    const { unmount } = renderPrompt();
    expect(screen.getByText('Welcome to DataQ')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
    // The mechanism itself: sessionStorage, not React state, is what has to
    // survive — a component remount (a route change, the next section below)
    // starts with fresh state and would re-show the prompt if this weren't here.
    expect(sessionStorage.getItem('dataq:profileCompletionPrompt:skipped')).toBe('1');

    // A fresh mount (e.g. a route re-render) must stay dismissed from the very
    // first render — no open→close transition to wait out, since `dismissed`
    // is seeded from sessionStorage before the first paint.
    unmount();
    renderPrompt();
    expect(screen.queryByText('Welcome to DataQ')).not.toBeInTheDocument();
    // And the save hook was never even reached.
    expect(save).not.toHaveBeenCalled();
  });
});
