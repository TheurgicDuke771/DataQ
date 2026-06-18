import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import { fetchMe } from '../../src/api/me';
import { CurrentUserContext, type CurrentUser } from '../../src/auth/currentUserContext';
import { MeProvider } from '../../src/auth/MeProvider';
import { useIsWorkspaceAdmin } from '../../src/auth/useMe';

vi.mock('../../src/api/me', () => ({ fetchMe: vi.fn() }));
const mockFetchMe = vi.mocked(fetchMe);

const devUser: CurrentUser = {
  name: 'Dev',
  username: 'dev@x.io',
  homeAccountId: 'acc-1',
  isDev: true,
};

const adminMe = {
  id: 'u1',
  aad_object_id: 'oid',
  email: 'dev@x.io',
  display_name: 'Dev',
  last_seen_at: null,
  is_workspace_admin: true,
};

/** Reads the shared admin flag so the test can observe MeContext. */
function Probe() {
  return <span data-testid="flag">admin:{String(useIsWorkspaceAdmin())}</span>;
}

function tree(user: CurrentUser | null): ReactNode {
  return (
    <CurrentUserContext.Provider value={user}>
      <MeProvider>
        <Probe />
      </MeProvider>
    </CurrentUserContext.Provider>
  );
}

afterEach(() => vi.clearAllMocks());

describe('MeProvider', () => {
  it('does not fetch /me until a user is present', () => {
    render(tree(null));
    expect(mockFetchMe).not.toHaveBeenCalled();
    expect(screen.getByTestId('flag')).toHaveTextContent('admin:false');
  });

  it('clears the admin flag on sign-out so it cannot linger (#173)', async () => {
    mockFetchMe.mockResolvedValue(adminMe);
    const { rerender } = render(tree(devUser));
    await waitFor(() => expect(screen.getByTestId('flag')).toHaveTextContent('admin:true'));

    // Sign out → user becomes null. The previous user's admin flag must not persist.
    rerender(tree(null));
    expect(screen.getByTestId('flag')).toHaveTextContent('admin:false');
  });
});
