import { api } from './client';

/** Per-suite alert notification config (W6; Slack + email per-suite added in #633). */

export type AlertOn = 'fail' | 'warn' | 'always';

/** Mirrors the backend `SuiteNotificationRead`. */
export interface SuiteNotification {
  /** False when the suite has no saved row (the values are the defaults). */
  configured: boolean;
  enabled: boolean;
  alert_on: AlertOn;
  has_webhook: boolean;
  has_slack_webhook: boolean;
  email_recipients: string | null;
}

/** Mirrors `SuiteNotificationUpdate`. */
export interface SuiteNotificationUpdate {
  enabled: boolean;
  alert_on: AlertOn;
  webhook?: string;
  slack_webhook?: string;
  email_recipients?: string;
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
