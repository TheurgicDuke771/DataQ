import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { PageError } from '../../src/components/feedback/PageError';

function renderError(props: Parameters<typeof PageError>[0]) {
  return render(
    <MemoryRouter>
      <PageError {...props} />
    </MemoryRouter>,
  );
}

describe('PageError (#910)', () => {
  it('renders the dedicated 500 page and surfaces the request id for tracing', () => {
    renderError({ error: 'Internal Server Error', httpStatus: 500, requestId: 'abc123' });
    expect(screen.getByText('500 — Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('abc123')).toBeInTheDocument();
  });

  it('keeps the catalog copy for 5xx rather than echoing raw server noise', () => {
    renderError({ error: 'psycopg2.ProgrammingError: relation does not exist', httpStatus: 500 });
    expect(screen.getByText('An unexpected error occurred on our side.')).toBeInTheDocument();
    // The raw backend message must not be rendered at the user.
    expect(screen.queryByText(/psycopg2/)).not.toBeInTheDocument();
  });

  it('shows the actionable backend message for a 4xx', () => {
    renderError({ error: 'this connection is in use by 2 suites', httpStatus: 409 });
    expect(screen.getByText('this connection is in use by 2 suites')).toBeInTheDocument();
  });

  it('maps an unknown 5xx onto the 500 page and an unknown 4xx onto 400', () => {
    const { unmount } = renderError({ error: 'boom', httpStatus: 507 });
    expect(screen.getByText('500 — Something went wrong')).toBeInTheDocument();
    unmount();
    renderError({ error: 'nope', httpStatus: 418 });
    expect(screen.getByText('400 — Bad request')).toBeInTheDocument();
  });

  it('reports a NETWORK failure as 503 and keeps its message', () => {
    // Request went out, nothing came back — "Service unavailable" is honest, and
    // the message is KEPT because no server answered with anything better.
    renderError({ error: 'Network Error', kind: 'network' });
    expect(screen.getByText('503 — Service unavailable')).toBeInTheDocument();
    expect(screen.getByText('Network Error')).toBeInTheDocument();
  });

  it('does NOT blame the service for a client-side failure (#930 review)', () => {
    // A throw that never reached the network (an auth redirect rejecting
    // in-flight, a TypeError in page code) used to paint a confident
    // "503 — Service unavailable" over a perfectly healthy backend, sending
    // the user — and support — after an outage that was not happening.
    renderError({ error: 'login_required', kind: 'client' });
    expect(screen.queryByText('503 — Service unavailable')).not.toBeInTheDocument();
    expect(screen.getByText('500 — Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('login_required')).toBeInTheDocument();
  });

  it('offers an in-place retry when given one, instead of a full reload', async () => {
    const onRetry = vi.fn();
    renderError({ error: 'boom', httpStatus: 500, onRetry });
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('renders a dedicated 404 page for a missing resource', () => {
    renderError({ error: 'asset not found', httpStatus: 404 });
    expect(screen.getByText('404 — Not found')).toBeInTheDocument();
  });
});
