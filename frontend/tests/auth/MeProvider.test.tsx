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

// ── a throttled first /me must not become an error page (#788) ──────────────

describe('MeProvider under rate limiting', () => {
  // Restore in afterEach, NOT at the end of each test body: a test that FAILS
  // never reaches its last line, so fake timers leak into the next test and it
  // times out for a reason that has nothing to do with what it asserts. Found by
  // mutating the retry away — three real failures plus one cascaded phantom.
  afterEach(() => vi.useRealTimers());

  const throttled = (retryAfterSeconds: number) =>
    Object.assign(new Error('Too many requests'), {
      response: {
        status: 429,
        data: { error: { detail: { retry_after_seconds: retryAfterSeconds } } },
        headers: {},
      },
    });

  it('retries a 429 instead of painting an error, and shows the answer when it lands', async () => {
    // The reachable case: the per-IP unauth bucket is shared across endpoints, so
    // a burst anywhere can 429 the FIRST /me — and an admin would lose the Admin
    // nav to a failure that resolves itself in seconds.
    vi.useFakeTimers();
    mockFetchMe.mockRejectedValueOnce(throttled(1)).mockResolvedValueOnce(adminMe);

    render(tree(devUser));
    await vi.advanceTimersByTimeAsync(0);
    // Still loading — NOT an error. The spinner is the honest state here.
    expect(screen.getByTestId('flag')).toHaveTextContent('admin:false');
    expect(mockFetchMe).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    await vi.waitFor(() => expect(screen.getByTestId('flag')).toHaveTextContent('admin:true'));
    expect(mockFetchMe).toHaveBeenCalledTimes(2);
  });

  it('waits the server-specified delay rather than retrying immediately', async () => {
    // Retrying straight away would spend the user's next window on a request we
    // were told would fail, and hammer a limiter that is already saying stop.
    vi.useFakeTimers();
    mockFetchMe.mockRejectedValueOnce(throttled(30)).mockResolvedValueOnce(adminMe);

    render(tree(devUser));
    await vi.advanceTimersByTimeAsync(5_000);
    expect(mockFetchMe).toHaveBeenCalledTimes(1); // 5s in, still waiting

    await vi.advanceTimersByTimeAsync(26_000);
    await vi.waitFor(() => expect(mockFetchMe).toHaveBeenCalledTimes(2));
  });

  it('gives up on a persistent 429 rather than spinning forever', async () => {
    // An endless spinner is the one outcome worse than an error page: the user
    // cannot tell it from a hung app, and there is nothing to act on.
    vi.useFakeTimers();
    mockFetchMe.mockRejectedValue(throttled(1));

    render(tree(devUser));
    for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(1_000);

    // Bounded: the initial attempt plus a fixed number of retries, then it stops.
    await vi.waitFor(() => expect(mockFetchMe).toHaveBeenCalledTimes(4));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(mockFetchMe).toHaveBeenCalledTimes(4);
  });

  it('still surfaces a non-429 failure immediately', async () => {
    // The retry is for throttling only — a 500 or a 403 is not self-healing and
    // must reach PageError with its HTTP facts intact (#910/#930).
    mockFetchMe.mockRejectedValue(new Error('boom'));

    render(tree(devUser));
    await waitFor(() => expect(mockFetchMe).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('flag')).toHaveTextContent('admin:false'));
  });
});
