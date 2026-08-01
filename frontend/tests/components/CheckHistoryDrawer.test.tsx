import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type CheckVersion, listCheckVersions, restoreCheckVersion } from '../../src/api/suites';
import { CheckHistoryDrawer } from '../../src/components/checks/CheckHistoryDrawer';

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listCheckVersions: vi.fn(), restoreCheckVersion: vi.fn() };
});

const mockList = vi.mocked(listCheckVersions);
const mockRestore = vi.mocked(restoreCheckVersion);

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

function renderDrawer(
  check: { id: string; name: string } | null = { id: 'c1', name: 'orders' },
  opts: { canRestore?: boolean; onRestored?: () => void } = {},
) {
  return render(
    <AntApp>
      <CheckHistoryDrawer
        open
        suiteId="s1"
        check={check}
        canRestore={opts.canRestore}
        onRestored={opts.onRestored}
        onClose={vi.fn()}
      />
    </AntApp>,
  );
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

// ───────────────────────── restore (#283) ────────────────────────────────

describe('CheckHistoryDrawer — restore', () => {
  it('offers no Restore button when canRestore is false (default, view-only)', async () => {
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    renderDrawer();

    await screen.findByText('v2');
    expect(screen.queryByRole('button', { name: /Restore this version/ })).not.toBeInTheDocument();
  });

  it('offers Restore only on non-current rows when canRestore is true', async () => {
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    renderDrawer(undefined, { canRestore: true });

    await screen.findByText('v2');
    // Exactly one Restore button — the older, non-current v1 row.
    expect(screen.getAllByRole('button', { name: /Restore this version/ })).toHaveLength(1);
  });

  it('restores on confirm, toasts success, and refreshes the list + calls onRestored', async () => {
    const user = userEvent.setup();
    const onRestored = vi.fn();
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    mockRestore.mockResolvedValue({
      id: 'c1',
      suite_id: 's1',
      name: 'orders not null',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: 'order_id' },
      warn_threshold: null,
      fail_threshold: null,
      critical_threshold: null,
      alert_snoozed_until: null,
    });
    renderDrawer(undefined, { canRestore: true, onRestored });

    await user.click(await screen.findByRole('button', { name: /Restore this version/ }));
    await user.click(await screen.findByRole('button', { name: 'Restore' }));

    await waitFor(() => expect(mockRestore).toHaveBeenCalledWith('s1', 'c1', 1));
    expect(await screen.findByText('Restored v1')).toBeInTheDocument();
    expect(onRestored).toHaveBeenCalledTimes(1);
    // The list is refetched (refreshKey bump) without closing the drawer.
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it('toasts an error and leaves history untouched when restore fails', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([version({ version_no: 2 }), version({ version_no: 1 })]);
    mockRestore.mockRejectedValue(new Error('snapshot no longer valid'));
    renderDrawer(undefined, { canRestore: true });

    // Rethrowing to keep the Popconfirm open (the fix under test) is exactly
    // what makes antd's own ActionButton produce a SECOND, internal derived
    // promise it deliberately never catches (ant-design/ant-design#6183) — the
    // library's own documented signal, not a bug in this component. Vitest's
    // unhandled-rejection reporter skips an event that has more than its own
    // listener attached, so register a no-op one for this window only.
    const swallowExpectedRejection = () => {};
    process.on('unhandledRejection', swallowExpectedRejection);
    try {
      await user.click(await screen.findByRole('button', { name: /Restore this version/ }));
      await user.click(await screen.findByRole('button', { name: 'Restore' }));

      expect(
        await screen.findByText(/Restore failed: snapshot no longer valid/),
      ).toBeInTheDocument();
    } finally {
      process.off('unhandledRejection', swallowExpectedRejection);
    }
    // No refetch on failure — still the one initial load.
    expect(mockList).toHaveBeenCalledTimes(1);
    // The Popconfirm itself must stay OPEN on failure (its OK button re-throws
    // into antd's ActionButton, which only closes on a resolved promise) — a
    // silent close here would read as "restored" when it wasn't (#204 drift).
    // The OK button (matched loosely: its own internal loading spinner is
    // mid-exit-transition and still contributes a "loading " prefix to its
    // accessible name) must still be present AND not mid-close: jsdom never
    // fires a real CSS transitionend, so a genuinely-closing Popconfirm's
    // buttons keep rendering (in an `ant-zoom-big-leave` shell) long after
    // `close()` was called — the one signal set SYNCHRONOUSLY the moment it
    // closes, before any animation, is `pointer-events: none` on the popover
    // container, so check that instead of presence alone.
    const okButton = screen.getByRole('button', { name: /^(loading )?Restore$/ });
    const popover = okButton.closest<HTMLElement>('.ant-popover');
    expect(popover).not.toBeNull();
    expect(popover?.style.pointerEvents).not.toBe('none');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });
});
