import { api } from './client';

/** Reusable notification channels (#1514/#1662/#1663/#1761) — a destination defined once and
 *  linked to many suites, admin-gated CRUD (backend `channel_service` + `/notification-channels`).
 */

export const CHANNEL_TYPES = ['teams', 'slack', 'email', 'webhook'] as const;
export type ChannelType = (typeof CHANNEL_TYPES)[number];

export const CHANNEL_TYPE_LABELS: Record<ChannelType, string> = {
  teams: 'Microsoft Teams',
  slack: 'Slack',
  email: 'Email',
  webhook: 'Generic webhook',
};

/** Mirrors the backend `ChannelRead`. `payload_template`/`hmac_secret` are populated
 *  only in the shapes the backend documents (admin-only / create-or-rotate-once). */
export interface NotificationChannel {
  id: string;
  name: string;
  type: ChannelType;
  has_webhook: boolean;
  email_recipients: string | null;
  webhook_url: string | null;
  has_hmac_secret: boolean;
  hmac_secret: string | null;
  payload_template: Record<string, unknown> | null;
  has_payload_template: boolean;
  auth_header_name: string | null;
  has_auth_header: boolean;
}

/** Mirrors `ChannelCreate`. */
export interface ChannelCreatePayload {
  name: string;
  type: ChannelType;
  webhook?: string;
  email_recipients?: string;
  webhook_url?: string;
  payload_template?: Record<string, unknown>;
  auth_header_name?: string;
  auth_header_value?: string;
}

/**
 * Mirrors `ChannelUpdate` — tri-state per the backend docstring: omit a field = unchanged, `""` =
 * clear, a value = set/rotate. `payload_template` can't use "" to mean clear (an empty object is a
 * legitimate template), so `clear_payload_template` is its own flag.
 */
export interface ChannelUpdatePayload {
  name?: string;
  webhook?: string;
  email_recipients?: string;
  webhook_url?: string;
  regenerate_hmac_secret?: boolean;
  payload_template?: Record<string, unknown>;
  clear_payload_template?: boolean;
  auth_header_name?: string;
  auth_header_value?: string;
}

export async function listChannels(): Promise<NotificationChannel[]> {
  const { data } = await api.get<NotificationChannel[]>('/notification-channels');
  return data;
}

export async function getChannel(id: string): Promise<NotificationChannel> {
  const { data } = await api.get<NotificationChannel>(`/notification-channels/${id}`);
  return data;
}

export async function createChannel(payload: ChannelCreatePayload): Promise<NotificationChannel> {
  const { data } = await api.post<NotificationChannel>('/notification-channels', payload);
  return data;
}

export async function updateChannel(
  id: string,
  payload: ChannelUpdatePayload,
): Promise<NotificationChannel> {
  const { data } = await api.patch<NotificationChannel>(`/notification-channels/${id}`, payload);
  return data;
}

/** May reject with a `channel_in_use` 409 (backend `ChannelInUseError`) — the caller
 *  should surface `detail.total` / `detail.suites`, not a generic failure toast. */
export async function deleteChannel(id: string): Promise<void> {
  await api.delete(`/notification-channels/${id}`);
}

export async function listSuiteChannels(suiteId: string): Promise<NotificationChannel[]> {
  const { data } = await api.get<NotificationChannel[]>(`/suites/${suiteId}/notification-channels`);
  return data;
}

/** Idempotent link (backend no-ops if already linked). */
export async function linkSuiteChannel(suiteId: string, channelId: string): Promise<void> {
  await api.put(`/suites/${suiteId}/notification-channels/${channelId}`);
}

export async function unlinkSuiteChannel(suiteId: string, channelId: string): Promise<void> {
  await api.delete(`/suites/${suiteId}/notification-channels/${channelId}`);
}
