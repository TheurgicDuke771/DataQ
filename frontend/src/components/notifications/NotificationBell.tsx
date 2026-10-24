import { BellOutlined } from '@ant-design/icons';
import { Alert, Badge, Button, Drawer, Empty, Flex, Spin, Tooltip, Typography } from 'antd';
import { type CSSProperties, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { retryAfterSeconds } from '../../api/client';
import {
  type FeedNotification,
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../../api/notificationFeed';
import { fetchFailure } from '../../utils/errors';
import { formatTimestamp } from '../results/resultsFormat';
import { ResultStatusTag } from '../shared/StatusTag';

/** How often the unread badge is refreshed while the tab is visible. One small GET a minute
 *  stays far inside the per-token rate budget even with several tabs open. */
export const NOTIFICATION_POLL_MS = 60_000;
/** The first page the drawer loads; the footer says when the feed holds more. */
const PAGE_SIZE = 50;

type Phase =
  | { kind: 'loading' }
  | { kind: 'ok'; items: FeedNotification[]; total: number }
  | { kind: 'error'; message: string };

function bellLabel(unread: number | null): string {
  if (unread === null) return 'Notifications';
  if (unread === 0) return 'Notifications, none unread';
  return unread === 1 ? 'Notifications, 1 unread' : `Notifications, ${unread} unread`;
}

const listReset: CSSProperties = { listStyle: 'none', margin: 0, padding: 0 };

/**
 * The header bell (#1687): an unread badge kept fresh by polling, and a drawer with the
 * signed-in user's feed — each entry a deep link to its run (and incident), a per-entry
 * read-ack and "Mark all read". Entries follow suite access: a user sees an alert only for a
 * suite they can see, and read state is theirs alone.
 */
export function NotificationBell() {
  const navigate = useNavigate();
  const bellRef = useRef<HTMLButtonElement>(null);
  const [unread, setUnread] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [announcement, setAnnouncement] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Whether to hand focus back to the bell when the drawer closes — not after following a link,
  // where the destination page takes over.
  const restoreFocus = useRef(true);
  const lastUnread = useRef<number | null>(null);
  const openRef = useRef(false);
  useEffect(() => {
    openRef.current = open;
  }, [open]);

  // ── unread badge: poll while the tab is visible, pause while it is hidden ──
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;

    const schedule = (ms: number) => {
      if (cancelled) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(tick, ms);
    };
    const tick = async () => {
      if (cancelled) return;
      if (document.visibilityState === 'hidden') return; // resumes on visibilitychange
      // One poll at a time: a visibilitychange tick while the interval timer is still armed
      // would otherwise run two, and orphan the first controller.
      window.clearTimeout(timer);
      controller?.abort();
      controller = new AbortController();
      try {
        const count = await getUnreadCount(controller.signal);
        if (cancelled) return;
        setUnread(count);
        const previous = lastUnread.current;
        // Announce only a rise the user did not cause — never the first load, never while
        // they are already looking at the drawer.
        if (previous !== null && count > previous && !openRef.current) {
          const added = count - previous;
          setAnnouncement(added === 1 ? '1 new notification' : `${added} new notifications`);
        }
        lastUnread.current = count;
        schedule(NOTIFICATION_POLL_MS);
      } catch (err: unknown) {
        if (cancelled || controller.signal.aborted) return;
        // A throttle says when to come back; anything else waits a normal interval. The
        // badge keeps its last known value rather than flashing to zero.
        const retryAfter = retryAfterSeconds(err);
        schedule(retryAfter !== undefined ? retryAfter * 1000 : NOTIFICATION_POLL_MS);
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void tick();
    };
    document.addEventListener('visibilitychange', onVisibility);
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  // ── the drawer's page: fetched on every open and on Retry (`loadNonce`) ──
  const [loadNonce, setLoadNonce] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    listNotifications({ limit: PAGE_SIZE }, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) {
          setPhase({ kind: 'ok', items: page.items, total: page.total });
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setPhase({
          kind: 'error',
          message: fetchFailure(err, 'Failed to load notifications').message,
        });
      });
    return () => controller.abort();
  }, [open, loadNonce]);

  const reloadPage = useCallback(() => {
    setPhase({ kind: 'loading' });
    setLoadNonce((n) => n + 1);
  }, []);

  const openDrawer = () => {
    restoreFocus.current = true;
    setActionError(null);
    setPhase({ kind: 'loading' });
    setOpen(true);
  };
  const closeDrawer = () => setOpen(false);

  const applyRead = (updated: FeedNotification) => {
    setPhase((current) =>
      current.kind === 'ok'
        ? { ...current, items: current.items.map((n) => (n.id === updated.id ? updated : n)) }
        : current,
    );
    setUnread((current) => {
      const next = current === null ? null : Math.max(0, current - 1);
      lastUnread.current = next;
      return next;
    });
  };

  const markRead = async (item: FeedNotification): Promise<boolean> => {
    if (item.read_at) return true;
    setActionError(null);
    try {
      applyRead(await markNotificationRead(item.id));
      return true;
    } catch (err: unknown) {
      setActionError(fetchFailure(err, 'Could not mark the notification read').message);
      return false;
    }
  };

  const markAll = async () => {
    setBusy(true);
    setActionError(null);
    try {
      const marked = await markAllNotificationsRead();
      const stamp = new Date().toISOString();
      setPhase((current) =>
        current.kind === 'ok'
          ? { ...current, items: current.items.map((n) => ({ ...n, read_at: n.read_at ?? stamp })) }
          : current,
      );
      setUnread(0);
      lastUnread.current = 0;
      setAnnouncement(
        marked === 1 ? '1 notification marked read' : `${marked} notifications marked read`,
      );
    } catch (err: unknown) {
      setActionError(fetchFailure(err, 'Could not mark all notifications read').message);
    } finally {
      setBusy(false);
    }
  };

  const follow = async (item: FeedNotification, href: string) => {
    // Read-ack first; the navigation goes ahead even if the ack fails (the error shows the
    // next time the drawer opens, and the entry simply stays unread).
    await markRead(item);
    restoreFocus.current = false;
    setOpen(false);
    navigate(href);
  };

  // While the count poll has not yet succeeded, the loaded page is the next best truth —
  // a 429'd first poll must not disable "Mark all read" over a drawer full of unread items.
  const hasUnread =
    unread !== null
      ? unread > 0
      : phase.kind === 'ok' && phase.items.some((item) => item.read_at === null);

  return (
    <>
      <Tooltip title="Notifications">
        <Badge
          // The count is in the bell's own name; the red number is a visual duplicate, so it
          // carries no title and is hidden from assistive tech (the Badge wrapper itself
          // stays visible to it, or the button inside would vanish from the tree too).
          count={
            hasUnread ? (
              <span aria-hidden className="dq-bell-count">
                {(unread ?? 0) > 99 ? '99+' : unread}
              </span>
            ) : (
              0
            )
          }
          size="small"
          title=""
        >
          <Button
            ref={bellRef}
            type="text"
            icon={<BellOutlined aria-hidden />}
            aria-label={bellLabel(unread)}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={openDrawer}
          />
        </Badge>
      </Tooltip>
      <span role="status" aria-live="polite" className="dq-sr-only">
        {announcement}
      </span>
      <Drawer
        title="Notifications"
        open={open}
        onClose={closeDrawer}
        size={440}
        afterOpenChange={(isOpen) => {
          if (!isOpen && restoreFocus.current) bellRef.current?.focus();
        }}
        extra={
          <Button size="small" onClick={markAll} loading={busy} disabled={!hasUnread}>
            Mark all read
          </Button>
        }
      >
        {actionError && (
          <Alert type="error" showIcon title={actionError} style={{ marginBottom: 12 }} />
        )}
        {phase.kind === 'loading' && (
          <Flex justify="center" style={{ padding: 24 }}>
            <Spin description="Loading notifications…" />
          </Flex>
        )}
        {phase.kind === 'error' && (
          <Alert
            type="error"
            showIcon
            title="Failed to load notifications"
            description={phase.message}
            action={
              <Button size="small" onClick={reloadPage}>
                Retry
              </Button>
            }
          />
        )}
        {phase.kind === 'ok' && phase.items.length === 0 && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="No notifications yet. Alerts for the suites you can see will appear here as runs finish."
          />
        )}
        {phase.kind === 'ok' && phase.items.length > 0 && (
          <>
            <ul aria-label="Notifications" style={listReset}>
              {phase.items.map((item) => (
                <FeedItem key={item.id} item={item} onFollow={follow} onMarkRead={markRead} />
              ))}
            </ul>
            {phase.total > phase.items.length && (
              <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
                Showing the {phase.items.length} most recent of {phase.total}.
              </Typography.Text>
            )}
          </>
        )}
      </Drawer>
    </>
  );
}

function FeedItem({
  item,
  onFollow,
  onMarkRead,
}: {
  item: FeedNotification;
  onFollow: (item: FeedNotification, href: string) => Promise<void>;
  onMarkRead: (item: FeedNotification) => Promise<boolean>;
}) {
  const unread = item.read_at === null;
  const [acking, setAcking] = useState(false);
  return (
    <li
      style={{
        padding: '10px 0',
        borderBottom: '1px solid var(--dq-border)',
      }}
    >
      <Flex vertical gap={4}>
        <Flex align="center" gap={8} wrap>
          <ResultStatusTag status={item.severity} />
          {unread && <span className="dq-sr-only">Unread.</span>}
          {item.deep_link ? (
            <Link
              to={item.deep_link}
              className="dq-text-link"
              style={{ fontWeight: unread ? 600 : 400 }}
              onClick={(e) => {
                e.preventDefault();
                void onFollow(item, item.deep_link as string);
              }}
            >
              {item.title}
            </Link>
          ) : (
            <Typography.Text style={{ fontWeight: unread ? 600 : 400 }}>
              {item.title}
            </Typography.Text>
          )}
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {item.body}
        </Typography.Text>
        <Flex align="center" gap={12} wrap>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <time dateTime={item.created_at}>{formatTimestamp(item.created_at)}</time>
          </Typography.Text>
          {item.incident_link && (
            <Link
              to={item.incident_link}
              className="dq-text-link"
              style={{ fontSize: 12 }}
              aria-label={`Open incident: ${item.title}`}
              onClick={(e) => {
                e.preventDefault();
                void onFollow(item, item.incident_link as string);
              }}
            >
              Open incident
            </Link>
          )}
          {!item.deep_link && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Run no longer available
            </Typography.Text>
          )}
          {unread && (
            <Button
              type="link"
              size="small"
              style={{ padding: 0, height: 'auto', fontSize: 12 }}
              loading={acking}
              aria-label={`Mark read: ${item.title}`}
              onClick={async () => {
                setAcking(true);
                try {
                  await onMarkRead(item);
                } finally {
                  setAcking(false);
                }
              }}
            >
              Mark read
            </Button>
          )}
        </Flex>
      </Flex>
    </li>
  );
}
