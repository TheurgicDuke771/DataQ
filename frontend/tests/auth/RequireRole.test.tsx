import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { WorkspaceRole } from '../../src/api/admin';
import { RequireRole } from '../../src/auth/RequireRole';

let role: WorkspaceRole | null = 'admin';
vi.mock('../../src/auth/useMe', () => ({ useWorkspaceRole: () => role }));

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
    role = r;
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
    // `null` is "not yet known", NOT "denied" — flashing Forbidden at someone who
    // turns out to be an admin is worse than a spinner.
    role = null;
    renderGuard('admin');
    expect(screen.queryByText('protected content')).not.toBeInTheDocument();
    expect(
      screen.queryByText(/don't have access|not have access|Forbidden/i),
    ).not.toBeInTheDocument();
  });
});
