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

  it('treats a status-less failure as 503 AND keeps its message', () => {
    // Axios network errors carry no response; "Service unavailable" is the honest
    // reading, and must not be reported as a client-side 400. Unlike a real 5xx
    // response, the message is KEPT — no server answered, so what the client
    // caught is the only information available.
    renderError({ error: 'Network Error' });
    expect(screen.getByText('503 — Service unavailable')).toBeInTheDocument();
    expect(screen.getByText('Network Error')).toBeInTheDocument();
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
