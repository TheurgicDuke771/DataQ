import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listAdminAccess, listAdminSuites, listAdminUsers } from '../../../src/api/admin';
import { AdminOverview } from '../../../src/pages/admin/AdminOverview';
import { ACCESS, SUITE, USER, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAdminSuites: vi.fn(),
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
}));

const mockSuites = vi.mocked(listAdminSuites);
const mockUsers = vi.mocked(listAdminUsers);
const mockAccess = vi.mocked(listAdminAccess);

beforeEach(() => {
  mockSuites.mockResolvedValue([SUITE]);
  mockUsers.mockResolvedValue([USER]);
  mockAccess.mockResolvedValue(ACCESS);
});
afterEach(() => vi.clearAllMocks());

describe('AdminOverview', () => {
  it('counts suites, members and access grants', async () => {
    renderSubPage(<AdminOverview />);
    expect(screen.getByText('Suites')).toBeInTheDocument();
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Access grants')).toBeInTheDocument();
    expect(await screen.findByText('2')).toBeInTheDocument(); // two access grants
  });

  it('says the health feed is not monitored rather than implying all-clear', () => {
    renderSubPage(<AdminOverview />);
    expect(screen.getByText(/Not monitored yet/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is being watched, not that everything is fine/)).toBeVisible();
  });

  it('links onward to the other sub-pages', () => {
    renderSubPage(<AdminOverview />);
    expect(screen.getByRole('link', { name: /Members & access grants/ })).toHaveAttribute(
      'href',
      '/admin/members',
    );
    expect(screen.getByRole('link', { name: 'All suites' })).toHaveAttribute(
      'href',
      '/admin/suites',
    );
  });
});
