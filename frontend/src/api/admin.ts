import { api } from './client';
import type { OrchestrationProvider } from './triggerBindings';

/**
 * Workspace-admin read API — the all-suites / all-users / access overview behind
 * the Admin page. Every endpoint is gated server-side by `require_workspace_admin`
 * (403 for non-admins); the page renders the Forbidden state on that 403.
 */

export interface AdminSuite {
  id: string;
  name: string;
  connection_name: string;
  connection_type: string;
  env: string;
  owner_id: string;
  owner_email: string;
  owner_name: string | null;
  check_count: number;
  share_count: number;
  created_at: string;
  updated_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
  created_at: string;
  owned_suite_count: number;
  shared_suite_count: number;
}

/** One (user → suite) access grant: an implicit owner or an explicit share. */
export interface AdminAccess {
  suite_id: string;
  suite_name: string;
  user_id: string;
  user_email: string;
  user_name: string | null;
  permission: string; // 'owner' | 'admin' | 'edit' | 'view'
}

export async function listAdminSuites(): Promise<AdminSuite[]> {
  const { data } = await api.get<AdminSuite[]>('/admin/suites');
  return data;
}

export async function listAdminUsers(): Promise<AdminUser[]> {
  const { data } = await api.get<AdminUser[]>('/admin/users');
  return data;
}

export async function listAdminAccess(): Promise<AdminAccess[]> {
  const { data } = await api.get<AdminAccess[]>('/admin/access');
  return data;
}

/** One orchestration provider's inbound-webhook config (#490). `inbound_url` is
 *  ready to paste into the provider's webhook field; for ADF it embeds the shared
 *  secret (`?token=…`) — secret-bearing, admin-only. */
export interface AdminWebhook {
  provider: OrchestrationProvider;
  auth: string;
  inbound_url: string;
  token_configured: boolean;
  signing_secret_name: string | null;
  connection_names: string[];
}

export async function listAdminWebhooks(): Promise<AdminWebhook[]> {
  const { data } = await api.get<AdminWebhook[]>('/admin/orchestration/webhooks');
  return data;
}

/** SMTP pre-flight test (#737, ADR 0032 decision 7). Sends a real message to the
 *  CALLER's own address over the configured `AUTH_EMAIL_*` mailer — there is no
 *  recipient input, so this can only ever mail the admin who invoked it. On
 *  failure the backend's error envelope names the failing transport stage
 *  (connect/tls/auth/send) in `error.detail.stage`; the axios response
 *  interceptor already folds the envelope's human message into `err.message`,
 *  so callers just need `errorMessage(err)`. */
export interface AuthEmailTestResult {
  status: string;
  to: string;
}

export async function testAuthEmail(): Promise<AuthEmailTestResult> {
  const { data } = await api.post<AuthEmailTestResult>('/admin/auth-email/test');
  return data;
}
