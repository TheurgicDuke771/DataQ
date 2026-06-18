import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Home } from '../../src/pages/Home';

// Home now reads the shared /me state from MeContext (MeProvider owns the fetch),
// so the test provides the context value directly rather than mocking the call.
function renderHome(state: AsyncState<MeResponse>) {
  return render(<MeContext.Provider value={state}>{<Home />}</MeContext.Provider>);
}

describe('Home', () => {
  it('renders the authenticated user on success', () => {
    renderHome({
      status: 'ok',
      data: {
        id: 'u-1',
        aad_object_id: 'oid-1',
        email: 'jane@example.com',
        display_name: 'Jane Doe',
        last_seen_at: null,
        is_workspace_admin: false,
      },
    });

    expect(screen.getByText('jane@example.com')).toBeInTheDocument();
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('oid-1')).toBeInTheDocument();
  });

  it('surfaces the error message when /me fails', () => {
    renderHome({ status: 'error', error: 'network down' });

    // Regression guard for #80: the Alert heading must render as visible text.
    expect(screen.getByText('Failed to load /api/v1/me')).toBeInTheDocument();
    expect(screen.getByText('network down')).toBeInTheDocument();
  });
});
