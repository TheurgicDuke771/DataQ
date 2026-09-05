import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { searchUsers, type UserSummary } from '../../src/api/shares';
import { UserSearchSelect } from '../../src/components/shared/UserSearchSelect';

vi.mock('../../src/api/shares', () => ({ searchUsers: vi.fn() }));
const mockSearch = vi.mocked(searchUsers);

const ADA: UserSummary = {
  id: 'u-ada',
  email: 'ada@example.com',
  display_name: 'Ada',
  role: 'member',
};
const BOB: UserSummary = {
  id: 'u-bob',
  email: 'bob@example.com',
  display_name: null,
  role: 'member',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('UserSearchSelect', () => {
  it('keeps the picked user visible after a later search replaces the options', async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <UserSearchSelect value={undefined} onChange={onChange} ariaLabel="Owner" />,
    );
    rerender(<UserSearchSelect value={ADA} onChange={onChange} ariaLabel="Owner" />);
    expect(screen.getAllByText('Ada · ada@example.com').length).toBeGreaterThan(0);

    mockSearch.mockResolvedValue([BOB]);
    fireEvent.change(screen.getByRole('combobox', { name: 'Owner' }), { target: { value: 'bo' } });
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('bo'), { timeout: 2000 });
    await waitFor(() => expect(screen.getByText('bob@example.com')).toBeInTheDocument());
    expect(screen.getAllByText('Ada · ada@example.com').length).toBeGreaterThan(0);
  });

  it('does not search below two characters and applies the exclude filter', async () => {
    const onChange = vi.fn();
    mockSearch.mockResolvedValue([ADA, BOB]);
    render(
      <UserSearchSelect
        value={undefined}
        onChange={onChange}
        exclude={(u) => u.id === BOB.id}
        ariaLabel="Owner"
      />,
    );
    const box = screen.getByRole('combobox', { name: 'Owner' });
    fireEvent.change(box, { target: { value: 'a' } });
    await new Promise((r) => setTimeout(r, 400));
    expect(mockSearch).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: 'ad' } });
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('ad'), { timeout: 2000 });
    await waitFor(() => expect(screen.getByText('Ada · ada@example.com')).toBeInTheDocument());
    expect(screen.queryByText('bob@example.com')).not.toBeInTheDocument();
  });
});
