import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getWorkspaceDimensions } from '../../src/api/dashboard';
import { WorkspaceDimensions } from '../../src/components/dashboard/WorkspaceDimensions';

vi.mock('../../src/api/dashboard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/dashboard')>()),
  getWorkspaceDimensions: vi.fn(),
}));
const mockGet = vi.mocked(getWorkspaceDimensions);

afterEach(() => vi.clearAllMocks());

describe('WorkspaceDimensions', () => {
  it('shows each dimension across the workspace and says what it covers', async () => {
    mockGet.mockResolvedValue({
      covered: [
        {
          dimension: 'timeliness',
          checks_total: 9,
          checks_passing: 6,
          checks_evaluated: 8,
          score: 75,
        },
      ],
      uncovered: ['accuracy'],
      unclassified_checks: 2,
    });
    render(<WorkspaceDimensions />);

    expect(
      await screen.findByText('Data quality by dimension, across the workspace'),
    ).toBeInTheDocument();
    expect(screen.getByText('Timeliness')).toBeInTheDocument();
    expect(screen.getByText('6/9 passing')).toBeInTheDocument();
    // The scope is stated, because the tiles around it cover only the viewer's suites.
    expect(screen.getByText(/including ones you cannot open/)).toBeInTheDocument();
    // No asset-level headline on the workspace cut.
    expect(screen.queryByText('Asset health score')).not.toBeInTheDocument();
  });

  it('names the workspace, not an asset, when there are no checks anywhere', async () => {
    mockGet.mockResolvedValue({ covered: [], uncovered: [], unclassified_checks: 0 });
    render(<WorkspaceDimensions />);
    expect(await screen.findByText(/No checks on the workspace yet/)).toBeInTheDocument();
  });

  it('degrades to a line of text when the rollup cannot be loaded', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    render(<WorkspaceDimensions />);
    expect(
      await screen.findByText('The workspace dimension rollup is unavailable right now.'),
    ).toBeInTheDocument();
  });
});
