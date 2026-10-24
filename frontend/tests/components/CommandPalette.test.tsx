import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { search, type SearchResponse } from '../../src/api/search';
import { CommandPalette } from '../../src/components/shared/CommandPalette';

vi.mock('../../src/api/search', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/search')>()),
  search: vi.fn(),
}));

const mockSearch = vi.mocked(search);

const EMPTY: SearchResponse = {
  query: 'zzz',
  groups: { suites: [], checks: [], connections: [], runs: [] },
  truncated_groups: [],
};

const FOUND: SearchResponse = {
  query: 'orders',
  groups: {
    suites: [{ id: 's1', label: 'Orders quality', sublabel: null, href: '/suites/s1' }],
    checks: [
      {
        id: 'c1',
        label: 'orders id not null',
        sublabel: 'Orders quality',
        href: '/suites/s1/checks/c1/edit',
      },
    ],
    connections: [],
    runs: [
      {
        id: 'r1',
        label: 'Orders quality',
        sublabel: 'succeeded',
        href: '/results/r1',
        when: '2026-10-24T10:00:00Z',
      },
    ],
  },
  truncated_groups: ['checks'],
};

function WhereAmI() {
  const { pathname } = useLocation();
  return <span data-testid="where">{pathname}</span>;
}

function renderPalette(compact = false) {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <button type="button">elsewhere</button>
      <CommandPalette compact={compact} />
      <main id="dq-main" tabIndex={-1}>
        <Routes>
          <Route path="*" element={<WhereAmI />} />
        </Routes>
      </main>
    </MemoryRouter>,
  );
}

const trigger = () => screen.getByRole('button', { name: /^Search/ });
const combobox = () => screen.getByRole('combobox', { name: 'Search DataQ' });

afterEach(() => vi.clearAllMocks());

describe('CommandPalette', () => {
  it('opens from the header button, focuses the search box, and Escape returns focus to it', async () => {
    const user = userEvent.setup();
    renderPalette();
    await user.click(trigger());
    await waitFor(() => expect(combobox()).toHaveFocus());
    expect(screen.getByRole('status')).toHaveTextContent('Type to search');

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger()).toHaveFocus());
  });

  it('opens on Ctrl/⌘+K from anywhere and returns focus to the element that had it', async () => {
    const user = userEvent.setup();
    renderPalette();
    const elsewhere = screen.getByRole('button', { name: 'elsewhere' });
    elsewhere.focus();
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(combobox()).toHaveFocus());

    await user.keyboard('{Escape}');
    await waitFor(() => expect(elsewhere).toHaveFocus());
  });

  it('the compact trigger keeps an accessible name that carries the shortcut', () => {
    renderPalette(true);
    expect(screen.getByRole('button', { name: /^Search \(/ })).toBeInTheDocument();
  });

  it('searches as you type, groups the hits, moves with the arrow keys and opens on Enter', async () => {
    const user = userEvent.setup();
    mockSearch.mockResolvedValue(FOUND);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'orders');

    const listbox = await screen.findByRole('listbox', { name: 'Search results' });
    const options = await within(listbox).findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      'Orders quality',
      'orders id not nullOrders quality',
      expect.stringMatching(/^Orders qualitysucceeded · /),
    ]);
    expect(within(listbox).getByRole('group', { name: 'Checks' })).toBeInTheDocument();
    // The truncation hint sits outside the listbox (a listbox exposes only its options) and is
    // read out through the live region as well.
    expect(listbox).not.toHaveTextContent('More checks match');
    expect(screen.getAllByText(/More checks match/)).toHaveLength(2);
    expect(mockSearch).toHaveBeenLastCalledWith('orders', expect.any(AbortSignal));
    expect(screen.getByRole('status')).toHaveTextContent('3 results shown. More checks match');

    // The first hit is highlighted; the arrow keys move the highlight, Enter opens it.
    expect(combobox()).toHaveAttribute('aria-activedescendant', options[0].id);
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowDown}{ArrowDown}');
    expect(combobox()).toHaveAttribute('aria-activedescendant', options[2].id);
    await user.keyboard('{ArrowDown}');
    expect(combobox()).toHaveAttribute('aria-activedescendant', options[0].id);
    await user.keyboard('{ArrowUp}');
    expect(combobox()).toHaveAttribute('aria-activedescendant', options[2].id);
    await user.keyboard('{Enter}');

    expect(screen.getByTestId('where')).toHaveTextContent('/results/r1');
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument());
    // Focus lands on the destination page, not back on the button that opened the palette.
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
    expect(trigger()).not.toHaveFocus();
  });

  it('a trailing space or an unchanged term does not blank the results or search again', async () => {
    const user = userEvent.setup();
    mockSearch.mockResolvedValue(FOUND);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'orders');
    await screen.findAllByRole('option');
    await user.type(combobox(), ' ');
    await new Promise((r) => setTimeout(r, 300));
    expect(mockSearch).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole('option')).toHaveLength(3);
    expect(screen.getByRole('status')).not.toHaveTextContent('Searching');
  });

  it('leaves Ctrl/⌘+K alone inside another dialog, when already handled, and on auto-repeat', () => {
    render(
      <MemoryRouter>
        <div role="dialog" aria-label="Other">
          <button type="button">in dialog</button>
        </div>
        <CommandPalette />
      </MemoryRouter>,
    );
    const inDialog = screen.getByRole('button', { name: 'in dialog' });
    inDialog.focus();
    fireEvent.keyDown(inDialog, { key: 'k', ctrlKey: true });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    const handled = new KeyboardEvent('keydown', {
      key: 'k',
      ctrlKey: true,
      cancelable: true,
      bubbles: true,
    });
    handled.preventDefault();
    window.dispatchEvent(handled);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true, repeat: true });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('combobox', { name: 'Search DataQ' })).toBeInTheDocument();
  });

  it('clicking a hit opens it', async () => {
    const user = userEvent.setup();
    mockSearch.mockResolvedValue(FOUND);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'orders');
    await user.click(await screen.findByRole('option', { name: /orders id not null/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/suites/s1/checks/c1/edit');
  });

  it('says so when nothing matches, and when the search fails', async () => {
    const user = userEvent.setup();
    mockSearch.mockResolvedValueOnce(EMPTY);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'zzz');
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('No matches for “zzz”'),
    );
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(combobox()).toHaveAttribute('aria-expanded', 'false');

    mockSearch.mockRejectedValueOnce(new Error('backend down'));
    await user.type(combobox(), 'y');
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('backend down'));
  });

  it('drops a late answer to an earlier query', async () => {
    const user = userEvent.setup();
    let resolveFirst: (value: SearchResponse) => void = () => {};
    mockSearch
      .mockImplementationOnce(
        (_q, signal) =>
          new Promise<SearchResponse>((resolve, reject) => {
            resolveFirst = resolve;
            signal?.addEventListener('abort', () => reject(new Error('aborted')));
          }),
      )
      .mockResolvedValueOnce(EMPTY);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'ord');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledTimes(1));
    await user.type(combobox(), 'ers');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('No matches'));

    resolveFirst(FOUND);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('No matches');
  });

  it('clears the query when reopened', async () => {
    const user = userEvent.setup();
    mockSearch.mockResolvedValue(FOUND);
    renderPalette();
    await user.click(trigger());
    await user.type(combobox(), 'orders');
    await screen.findAllByRole('option');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument());
    await user.click(trigger());
    expect(combobox()).toHaveValue('');
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
  });
});
