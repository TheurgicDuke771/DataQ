import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listAdminSuites } from '../../../src/api/admin';
import { AdminSuites } from '../../../src/pages/admin/AdminSuites';
import { SUITE, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({ listAdminSuites: vi.fn() }));
const mockSuites = vi.mocked(listAdminSuites);

beforeEach(() => mockSuites.mockResolvedValue([SUITE]));
afterEach(() => vi.clearAllMocks());

describe('AdminSuites', () => {
  it('lists every suite in the workspace with its owner and datasource', async () => {
    renderSubPage(<AdminSuites />);
    expect(await screen.findByText('Finance DQ')).toBeInTheDocument();
    expect(screen.getByText('Olive Owner')).toBeInTheDocument();
    expect(screen.getByText('snowflake')).toBeInTheDocument();
  });

  it('surfaces a load error inline', async () => {
    // `…Once`, not a persistent rejection: RTL's cleanup re-invokes the fetcher
    // after the test body, and a still-rejecting mock surfaces there unhandled.
    mockSuites.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<AdminSuites />);
    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
  });
});
