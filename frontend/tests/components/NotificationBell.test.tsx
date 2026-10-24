import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type FeedNotification,
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../../src/api/notificationFeed';
import {
  NOTIFICATION_POLL_MS,
  NotificationBell,
} from '../../src/components/notifications/NotificationBell';

vi.mock('../../src/api/notificationFeed', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/notificationFeed')>()),
  getUnreadCount: vi.fn(),
  listNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));

const mockUnread = vi.mocked(getUnreadCount);
const mockList = vi.mocked(listNotifications);
const mockMarkRead = vi.mocked(markNotificationRead);
const mockMarkAll = vi.mocked(markAllNotificationsRead);

const UNREAD: FeedNotification = {
  id: 'n1',
  suite_id: 's1',
  run_id: 'r1',
  incident_id: 'i1',
  severity: 'fail',
  title: 'orders: 1 of 2 checks breached (worst: fail)',
  body: 'RETAIL.ORDERS (dev): id not null (fail)',
  deep_link: '/results/r1',
  incident_link: '/assets/a1?incident=i1',
  created_at: '2026-10-24T10:00:00Z',
  read_at: null,
};

const READ_NO_RUN: FeedNotification = {
  id: 'n2',
  suite_id: 's1',
  run_id: null,
  incident_id: null,
  severity: 'error',
  title: 'orders: run failed to execute',
  body: 'The run on RETAIL.ORDERS did not complete.',
  deep_link: null,
  incident_link: null,
  created_at: '2026-10-23T10:00:00Z',
  read_at: '2026-10-23T11:00:00Z',
};

function WhereAmI() {
  const { pathname, search } = useLocation();
  return <span data-testid="where">{pathname + search}</span>;
}

function renderBell() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <NotificationBell />
      <Routes>
        <Route path="*" element={<WhereAmI />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockUnread.mockResolvedValue(1);
  mockList.mockResolvedValue({ items: [UNREAD, READ_NO_RUN], total: 2 });
  mockMarkRead.mockImplementation(async (id) => ({
    ...UNREAD,
    id,
    read_at: '2026-10-24T12:00:00Z',
  }));
  mockMarkAll.mockResolvedValue(1);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('NotificationBell', () => {
  it('names the bell with the unread count and opens the feed', async () => {
    renderBell();
    const bell = await screen.findByRole('button', { name: 'Notifications, 1 unread' });
    expect(bell).toHaveAttribute('aria-haspopup', 'dialog');
    expect(bell).toHaveAttribute('aria-expanded', 'false');

    const user = userEvent.setup();
    await user.click(bell);
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    expect(bell).toHaveAttribute('aria-expanded', 'true');
    expect(mockList).toHaveBeenCalledWith({ limit: 50 }, expect.any(AbortSignal));
    const list = within(dialog).getByRole('list', { name: 'Notifications' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    // The unread entry is a link to its run and says so to assistive tech.
    expect(within(items[0]).getByRole('link', { name: UNREAD.title })).toHaveAttribute(
      'href',
      '/results/r1',
    );
    expect(within(items[0]).getByText('Unread.')).toBeInTheDocument();
    expect(within(items[0]).getByText(UNREAD.body)).toBeInTheDocument();
    // An entry whose run is gone has no link and says why.
    expect(within(items[1]).queryByRole('link')).toBeNull();
    expect(within(items[1]).getByText('Run no longer available')).toBeInTheDocument();
    expect(within(items[1]).queryByRole('button', { name: /^Mark read/ })).toBeNull();
  });

  it('says none unread and disables "Mark all read" when the feed is clear', async () => {
    mockUnread.mockResolvedValue(0);
    mockList.mockResolvedValue({ items: [READ_NO_RUN], total: 1 });
    renderBell();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Notifications, none unread' }));
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    expect(within(dialog).getByRole('button', { name: 'Mark all read' })).toBeDisabled();
  });

  it('marks one entry read in place and lowers the badge', async () => {
    renderBell();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }));
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    await user.click(within(dialog).getByRole('button', { name: `Mark read: ${UNREAD.title}` }));
    expect(mockMarkRead).toHaveBeenCalledWith('n1');
    await waitFor(() =>
      expect(within(dialog).queryByRole('button', { name: /^Mark read/ })).toBeNull(),
    );
    expect(screen.getByRole('button', { name: 'Notifications, none unread' })).toBeInTheDocument();
    // No refetch of the page — the ack is applied to the row the server returned.
    expect(mockList).toHaveBeenCalledTimes(1);
  });

  it('marks everything read, announces it, and shows an error when the server refuses', async () => {
    renderBell();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }));
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    await user.click(within(dialog).getByRole('button', { name: 'Mark all read' }));
    expect(mockMarkAll).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('1 notification marked read'),
    );
    expect(within(dialog).queryByText('Unread.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Notifications, none unread' })).toBeInTheDocument();

    // A refused ack is shown, not swallowed.
    mockMarkRead.mockRejectedValueOnce(new Error('This is a read-only demo.'));
    mockList.mockResolvedValue({ items: [UNREAD], total: 1 });
    mockUnread.mockResolvedValue(1);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await user.click(screen.getByRole('button', { name: /^Notifications/ }));
    const reopened = await screen.findByRole('dialog', { name: 'Notifications' });
    await user.click(
      await within(reopened).findByRole('button', { name: `Mark read: ${UNREAD.title}` }),
    );
    expect(await within(reopened).findByText('This is a read-only demo.')).toBeInTheDocument();
  });

  it('following a deep link acks the entry, closes the drawer and navigates', async () => {
    renderBell();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Notifications, 1 unread' }));
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    await user.click(within(dialog).getByRole('link', { name: `Open incident: ${UNREAD.title}` }));
    expect(mockMarkRead).toHaveBeenCalledWith('n1');
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/assets/a1?incident=i1'),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('returns focus to the bell when the drawer closes without navigating', async () => {
    renderBell();
    const user = userEvent.setup();
    const bell = await screen.findByRole('button', { name: 'Notifications, 1 unread' });
    await user.click(bell);
    await screen.findByRole('dialog', { name: 'Notifications' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(bell).toHaveFocus());
  });

  it('shows the load error with a retry, and says when the feed holds more than the page', async () => {
    mockList.mockRejectedValueOnce(new Error('boom'));
    renderBell();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /^Notifications/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Notifications' });
    expect(await within(dialog).findByText('Failed to load notifications')).toBeInTheDocument();
    mockList.mockResolvedValue({ items: [UNREAD], total: 73 });
    await user.click(within(dialog).getByRole('button', { name: 'Retry' }));
    expect(await within(dialog).findByText('Showing the 1 most recent of 73.')).toBeInTheDocument();
  });

  it('polls the unread count, announces a rise, and keeps the last value on a failed poll', async () => {
    vi.useFakeTimers();
    mockUnread
      .mockResolvedValueOnce(1)
      .mockResolvedValueOnce(3)
      .mockRejectedValueOnce(new Error('x'));
    renderBell();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole('button', { name: 'Notifications, 1 unread' })).toBeInTheDocument();
    // The first load announces nothing.
    expect(screen.getByRole('status')).toHaveTextContent('');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTIFICATION_POLL_MS);
    });
    expect(mockUnread).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Notifications, 3 unread' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('2 new notifications');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTIFICATION_POLL_MS);
    });
    expect(mockUnread).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('button', { name: 'Notifications, 3 unread' })).toBeInTheDocument();
  });

  it('does not poll while the tab is hidden and catches up when it is shown again', async () => {
    vi.useFakeTimers();
    renderBell();
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockUnread).toHaveBeenCalledTimes(1);
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTIFICATION_POLL_MS * 3);
    });
    expect(mockUnread).toHaveBeenCalledTimes(1);
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
    });
    expect(mockUnread).toHaveBeenCalledTimes(2);
  });
});
