import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchMe } from '../../src/api/me';
import { Home } from '../../src/pages/Home';

vi.mock('../../src/api/me', () => ({ fetchMe: vi.fn() }));

const mockFetchMe = vi.mocked(fetchMe);

afterEach(() => {
  vi.clearAllMocks();
});

describe('Home', () => {
  it('renders the authenticated user on success', async () => {
    mockFetchMe.mockResolvedValue({
      id: 'u-1',
      aad_object_id: 'oid-1',
      email: 'jane@example.com',
      display_name: 'Jane Doe',
      last_seen_at: null,
    });

    render(<Home />);

    expect(await screen.findByText('jane@example.com')).toBeInTheDocument();
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('oid-1')).toBeInTheDocument();
  });

  it('surfaces the error message when /me fails', async () => {
    mockFetchMe.mockRejectedValue(new Error('network down'));

    render(<Home />);

    // The error detail is rendered via the Alert description.
    expect(await screen.findByText('network down')).toBeInTheDocument();
  });
});
