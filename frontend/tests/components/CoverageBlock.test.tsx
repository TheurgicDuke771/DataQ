import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AssetSummary, updateAsset } from '../../src/api/assets';
import { CoverageBlock } from '../../src/components/assets/CoverageBlock';
import { AutomaticTag } from '../../src/components/suites/AutomaticTag';

vi.mock('../../src/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/assets')>();
  return { ...actual, updateAsset: vi.fn() };
});

const mockUpdate = vi.mocked(updateAsset);

describe('CoverageBlock (ADR 0047)', () => {
  beforeEach(() => mockUpdate.mockReset());

  it('excluding a table sends auto_coverage_excluded and refreshes', async () => {
    mockUpdate.mockResolvedValue({} as AssetSummary);
    const onChanged = vi.fn();
    render(
      <App>
        <CoverageBlock assetId="a1" excluded={false} onChanged={onChanged} />
      </App>,
    );
    const toggle = screen.getByRole('switch', { name: 'Include this table in automatic coverage' });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(mockUpdate).toHaveBeenCalledWith('a1', { auto_coverage_excluded: true });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('an excluded table shows as not included', () => {
    render(
      <App>
        <CoverageBlock assetId="a1" excluded onChanged={vi.fn()} />
      </App>,
    );
    expect(screen.getByRole('switch')).not.toBeChecked();
  });
});

describe('AutomaticTag (ADR 0047)', () => {
  it('marks only automatic suites and checks', () => {
    const { rerender } = render(<AutomaticTag origin="auto" />);
    expect(screen.getByText('Automatic')).toBeInTheDocument();
    rerender(<AutomaticTag origin="user" />);
    expect(screen.queryByText('Automatic')).toBeNull();
    rerender(<AutomaticTag />);
    expect(screen.queryByText('Automatic')).toBeNull();
  });
});
