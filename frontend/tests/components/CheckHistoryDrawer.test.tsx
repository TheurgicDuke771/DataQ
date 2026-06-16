import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type CheckVersion, listCheckVersions } from '../../src/api/suites';
import { CheckHistoryDrawer } from '../../src/components/checks/CheckHistoryDrawer';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listCheckVersions: vi.fn() };
});

const mockList = vi.mocked(listCheckVersions);

function version(overrides: Partial<CheckVersion> = {}): CheckVersion {
  return {
    version_no: 1,
    name: 'orders not null',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_not_be_null',
    config: { column: 'order_id' },
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
    changed_by: 'u-1',
    changed_by_name: 'Ed Editor',
    created_at: '2026-06-15T10:00:00Z',
    ...overrides,
  };
}

function renderDrawer(check: { id: string; name: string } | null = { id: 'c1', name: 'orders' }) {
  return render(<CheckHistoryDrawer open suiteId="s1" check={check} onClose={vi.fn()} />);
}

afterEach(() => vi.clearAllMocks());

describe('CheckHistoryDrawer', () => {
  it('lists versions newest-first, tagging the latest as Current with author + config', async () => {
    mockList.mockResolvedValue([
      version({ version_no: 2, config: { column: 'amount' }, warn_threshold: 0.9 }),
      version({ version_no: 1, changed_by_name: 'Ada Author' }),
    ]);
    renderDrawer();

    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
    // Only the newest snapshot is the current saved state.
    expect(screen.getAllByText('Current')).toHaveLength(1);
    // Expectation label resolves from the catalog (not the raw type).
    expect(screen.getAllByText('Column values not null')).toHaveLength(2);
    // Author names and the config of each version render.
    expect(screen.getByText(/Ada Author/)).toBeInTheDocument();
    expect(screen.getByText(/"column":\s*"amount"/)).toBeInTheDocument();
    expect(screen.getByText('Warn ≥ 0.9')).toBeInTheDocument();
  });

  it('falls back to Unknown for a system/removed author', async () => {
    mockList.mockResolvedValue([version({ changed_by: null, changed_by_name: null })]);
    renderDrawer();

    expect(await screen.findByText(/Unknown/)).toBeInTheDocument();
  });

  it('shows an empty state for a check with no recorded history', async () => {
    mockList.mockResolvedValue([]);
    renderDrawer();

    expect(
      await screen.findByText(/No history yet — recording starts from the next save/),
    ).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderDrawer();

    expect(await screen.findByText('Failed to load history')).toBeInTheDocument();
  });

  it('does not fetch when no check is selected', () => {
    renderDrawer(null);
    expect(mockList).not.toHaveBeenCalled();
  });
});
