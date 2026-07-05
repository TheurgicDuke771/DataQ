import { api } from './client';

/**
 * Per-suite alert notification config (W6; Slack + email per-suite added in #633).
 * Decides whether a suite's run outcomes are delivered, at what threshold
 * (`alert_on`), and per channel *where* — a per-suite Teams webhook, Slack webhook,
 * and email recipients, each falling back to the workspace config when unset. The
 * webhook URLs are **write-only** secrets — sent on save, never returned (the read
 * exposes only `has_*_webhook`); email recipients aren't secret, so they ARE
 * returned for prefill. Managing needs `edit`; reading needs `view` (backend-gated).
 */

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

/**
 * Mirrors `SuiteNotificationUpdate`. The webhooks are tri-state (omit = unchanged,
 * "" = clear, value = set — they're write-only secrets). `email_recipients` is
 * returned + editable, so the form sends the current value (WYSIWYG: "" clears).
 */
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
