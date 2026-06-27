import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { NotFound } from '../../src/pages/NotFound';

describe('NotFound', () => {
  it('renders the 404 error page', () => {
    render(
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>,
    );
    expect(screen.getByText('404 — Not found')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to app' })).toBeInTheDocument();
  });
});
