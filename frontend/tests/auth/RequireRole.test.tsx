import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceRole } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { RequireRole } from '../../src/auth/RequireRole';
import type { AsyncState } from '../../src/hooks/useAsyncData';

let me: AsyncState<MeResponse> = { status: 'loading' };
vi.mock('../../src/auth/useMe', () => ({ useMe: () => me }));

function meAt(role: WorkspaceRole): AsyncState<MeResponse> {
  return {
    status: 'ok',
    data: {
      id: 'u-1',
      aad_object_id: 'oid-1',
      email: `${role}@dataq.io`,
      display_name: null,
      last_seen_at: null,
      role,
      is_workspace_admin: role === 'admin',
    },
  };
}

// The 403 page renders a "back home" Link, so the guard needs a router even
// though it never navigates itself.
function renderGuard(minimum: WorkspaceRole) {
  render(
    <MemoryRouter>
      <RequireRole minimum={minimum}>
        <div>protected content</div>
      </RequireRole>
    </MemoryRouter>,
  );
}

describe('RequireRole', () => {
  it.each([
    ['admin', 'admin', true],
    ['member', 'admin', false],
    ['viewer', 'admin', false],
    ['admin', 'member', true],
    ['member', 'member', true],
    ['viewer', 'member', false],
  ] as const)('role=%s minimum=%s → rendered=%s', (r, minimum, allowed) => {
    me = meAt(r);
    renderGuard(minimum);
    if (allowed) {
      expect(screen.getByText('protected content')).toBeInTheDocument();
    } else {
      expect(screen.queryByText('protected content')).not.toBeInTheDocument();
      // The 403 page, matched on its title rather than the default message —
      // the wording of that message is `Forbidden`'s to own, not this test's.
      expect(screen.getByText(/403 — Forbidden/)).toBeInTheDocument();
    }
  });

  it('shows neither the content nor Forbidden while /me is unresolved', () => {
    // `loading` is "not yet known", NOT "denied" — flashing Forbidden at someone who
    // turns out to be an admin is worse than a spinner.
    me = { status: 'loading' };
    renderGuard('admin');
    expect(screen.queryByText('protected content')).not.toBeInTheDocument();
    expect(
      screen.queryByText(/don't have access|not have access|Forbidden/i),
    ).not.toBeInTheDocument();
    expect(document.querySelector('.ant-spin')).toBeInTheDocument();
  });

  it('renders the classified error page — with the request id — when /me fails', () => {
    me = {
      status: 'error',
      error: 'upstream exploded',
      kind: 'http',
      httpStatus: 503,
      requestId: 'req-42',
    };
    renderGuard('admin');
    expect(screen.getByText(/503/)).toBeInTheDocument();
    expect(screen.getByText(/req-42/)).toBeInTheDocument();
    expect(screen.queryByText('protected content')).not.toBeInTheDocument();
    // Not Forbidden either: an unreachable /me says nothing about the caller's role.
    expect(screen.queryByText(/403 — Forbidden/)).not.toBeInTheDocument();
  });

  it('does not spin forever on a failed /me', () => {
    // The behaviour this replaced: `error` fell through to the loading branch, so a
    // broken /me was indistinguishable from a slow one — for every gated route.
    me = { status: 'error', error: 'boom', kind: 'client' };
    renderGuard('admin');
    expect(document.querySelector('.ant-spin')).not.toBeInTheDocument();
    expect(screen.getByText(/500/)).toBeInTheDocument();
  });
});
