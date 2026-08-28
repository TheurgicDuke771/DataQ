import { api } from './client';
import type { OrchestrationProvider } from './triggerBindings';

/** Workspace-admin read API — the all-suites / all-users / access overview behind the Admin page. */

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

/** The three workspace roles (ADR 0033). Closed vocabulary — the backend
 *  `Literal` rejects anything else with a 422. */
export type WorkspaceRole = 'admin' | 'member' | 'viewer';

export const WORKSPACE_ROLES: WorkspaceRole[] = ['admin', 'member', 'viewer'];

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
  created_at: string;
  owned_suite_count: number;
  shared_suite_count: number;
  /** The STORED role — what the editor writes. */
  role: WorkspaceRole;
  /** Whether the env allowlist grants this user admin regardless of `role`. */
  allowlist_admin: boolean;
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

/** Change a user's stored workspace role (ADR 0033, #742). */
export async function setAdminUserRole(userId: string, role: WorkspaceRole): Promise<AdminUser> {
  const { data } = await api.patch<AdminUser>(`/admin/users/${userId}/role`, { role });
  return data;
}

export async function listAdminAccess(): Promise<AdminAccess[]> {
  const { data } = await api.get<AdminAccess[]>('/admin/access');
  return data;
}

/** One orchestration provider's inbound-webhook config (#490). */
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

/** SMTP pre-flight test (#737, ADR 0032 decision 7). */
export interface AuthEmailTestResult {
  status: string;
  to: string;
}

export async function testAuthEmail(): Promise<AuthEmailTestResult> {
  const { data } = await api.post<AuthEmailTestResult>('/admin/auth-email/test');
  return data;
}

/** One append-only audit log row (ADR 0041 / G1, #1318). */
export interface AuditEvent {
  id: string;
  occurred_at: string;
  action_class: 'config' | 'access';
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_user_id: string | null;
  actor_kind: string;
  actor_label: string | null;
  actor_display: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string | null;
}

export interface AuditEventPage {
  events: AuditEvent[];
  total: number;
  /** `true` when this page is not the whole result — more rows exist past `limit`.
   *  Not currently rendered in the Admin UI: `total` (paired with a real antd
   *  pager) already conveys this more precisely — kept here for API-contract
   *  fidelity and because a non-paginated consumer of this type would need it. */
  truncated: boolean;
  /** The configured retention window in days. */
  retention_days: number;
  /** The point before which events have been swept — `null` when the sweep is disabled
   *  (0 or negative `retention_days`), which is a different statement from "nothing swept". */
  retained_since: string | null;
}

export interface AuditEventFilters {
  action_class?: 'config' | 'access';
  entity_type?: string;
  entity_id?: string;
  actor_user_id?: string;
  action?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export async function listAuditEvents(filters: AuditEventFilters = {}): Promise<AuditEventPage> {
  const params = Object.fromEntries(
    Object.entries(filters).filter(([, v]) => v !== undefined && v !== ''),
  );
  const { data } = await api.get<AuditEventPage>('/admin/audit-events', { params });
  return data;
}

/** One way data can leave the declared jurisdiction (G4/#434). */
export interface ExternalTransfer {
  name: string;
  enabled: boolean;
  detail: string;
}

export interface DeploymentPosture {
  /** The jurisdiction this deployment declares (`DEPLOYMENT_REGION`) — `null` = not declared. */
  region: string | null;
  external_transfers: ExternalTransfer[];
}

export async function getDeploymentPosture(): Promise<DeploymentPosture> {
  const { data } = await api.get<DeploymentPosture>('/admin/deployment');
  return data;
}
