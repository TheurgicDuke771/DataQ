import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ErrorState } from '../../src/components/feedback/ErrorState';

function renderState(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('ErrorState', () => {
  it('renders the catalog title + subtitle for a code', () => {
    renderState(<ErrorState code={404} />);
    expect(screen.getByText('404 — Not found')).toBeInTheDocument();
    expect(screen.getByText("This page doesn't exist or has moved.")).toBeInTheDocument();
  });

  it('overrides the subtitle with a message', () => {
    renderState(<ErrorState code={403} message="Admins only." />);
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
    expect(screen.getByText('Admins only.')).toBeInTheDocument();
  });

  it('offers a link home for client errors', () => {
    renderState(<ErrorState code={404} />);
    expect(screen.getByRole('button', { name: 'Back to app' })).toBeInTheDocument();
  });

  it('offers Reload + shows the request id for server errors', () => {
    renderState(<ErrorState code={503} requestId="req-abc" />);
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(screen.getByText('req-abc')).toBeInTheDocument();
  });

  it('does not show a request id for client errors', () => {
    renderState(<ErrorState code={404} requestId="req-abc" />);
    expect(screen.queryByText('req-abc')).not.toBeInTheDocument();
  });

  it('uses the retry handler when provided', async () => {
    const onRetry = vi.fn();
    renderState(<ErrorState code={500} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
