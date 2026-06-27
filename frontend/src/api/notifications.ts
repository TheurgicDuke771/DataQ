import { api } from './client';

/**
 * Per-suite alert notification config (W6). Decides whether a suite's run
 * outcomes are delivered, at what threshold (`alert_on`), and to which Teams
 * webhook. The webhook URL is **write-only** — sent on save, never returned
 * (it's a secret); the read surface exposes only `has_webhook`. Managing needs
 * `edit` on the suite (backend-gated); reading needs `view`.
 */

export type AlertOn = 'fail' | 'warn' | 'always';

/** Mirrors the backend `SuiteNotificationRead`. */
export interface SuiteNotification {
  /** False when the suite has no saved row (the values are the defaults). */
  configured: boolean;
  enabled: boolean;
  alert_on: AlertOn;
  has_webhook: boolean;
}

/** Mirrors `SuiteNotificationUpdate`. `webhook`: omit = unchanged, "" = clear. */
export interface SuiteNotificationUpdate {
  enabled: boolean;
  alert_on: AlertOn;
  webhook?: string;
}

export async function getNotifications(suiteId: string): Promise<SuiteNotification> {
  const { data } = await api.get<SuiteNotification>(`/suites/${suiteId}/notifications`);
  return data;
}

export async function putNotifications(
  suiteId: string,
  payload: SuiteNotificationUpdate,
): Promise<SuiteNotification> {
  const { data } = await api.put<SuiteNotification>(`/suites/${suiteId}/notifications`, payload);
  return data;
}

export async function deleteNotifications(suiteId: string): Promise<void> {
  await api.delete(`/suites/${suiteId}/notifications`);
}
