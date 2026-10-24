import { api } from './client';
import { type ListPage, toListPage } from './listPage';

/**
 * The signed-in user's in-app notification feed: every run outcome the alerting layer
 * published for a suite they can see. Read state is theirs alone; nothing here can reach
 * another user's rows.
 */

/** The run's worst result tier; `error` = the run failed to execute. */
export type NotificationSeverity = 'pass' | 'warn' | 'fail' | 'critical' | 'error';

/** Mirrors the backend `NotificationRead`. */
export interface FeedNotification {
  id: string;
  suite_id: string;
  run_id: string | null;
  incident_id: string | null;
  severity: NotificationSeverity;
  title: string;
  body: string;
  /** The run page while the run still exists; null once the entry has outlived it. */
  deep_link: string | null;
  /** The incident's view on its asset page, while the incident exists. */
  incident_link: string | null;
  created_at: string;
  read_at: string | null;
}

export async function listNotifications(
  params: { unread_only?: boolean; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<ListPage<FeedNotification>> {
  const res = await api.get<FeedNotification[]>('/notifications', { params, signal });
  return toListPage(res.data, res.headers);
}

export async function getUnreadCount(signal?: AbortSignal): Promise<number> {
  const { data } = await api.get<{ unread: number }>('/notifications/unread_count', { signal });
  return data.unread;
}

export async function markNotificationRead(id: string): Promise<FeedNotification> {
  const { data } = await api.post<FeedNotification>(`/notifications/${id}/read`);
  return data;
}

/** Returns how many entries this call flipped to read. */
export async function markAllNotificationsRead(): Promise<number> {
  const { data } = await api.post<{ marked: number }>('/notifications/read_all');
  return data.marked;
}
