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
  /** The share row a revoke targets — `null` on an implicit owner row, which is
   *  not a grant (transfer ownership instead). */
  grant_id: string | null;
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

/** Revoke any per-suite grant as a workspace admin, grant or suite unowned. */
export async function revokeAdminGrant(suiteId: string, grantId: string): Promise<void> {
  await api.delete(`/admin/suites/${suiteId}/access/${grantId}`);
}

export interface SuiteTransferResult {
  suite_id: string;
  previous_owner_id: string | null;
  new_owner_id: string;
  /** What the previous owner keeps — `null` when they keep nothing. */
  previous_owner_permission: string | null;
}

/** Hand a suite to another user — the offboarding primitive. */
export async function transferAdminSuite(
  suiteId: string,
  payload: { new_owner_user_id: string; keep_previous_owner_access: boolean },
): Promise<SuiteTransferResult> {
  const { data } = await api.post<SuiteTransferResult>(
    `/admin/suites/${suiteId}/transfer`,
    payload,
  );
  return data;
}

/** Delete any suite in the workspace. The cascade is the owner's own delete. */
export async function deleteAdminSuite(suiteId: string): Promise<void> {
  await api.delete(`/admin/suites/${suiteId}`);
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
  /** `true` when more rows exist past `limit`. Not rendered directly — `total` in
   *  the antd pager already conveys it — kept for API-contract fidelity. */
  truncated: boolean;
  retention_days: number;
  /** `null` when the sweep is disabled — distinct from "nothing swept yet". */
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
  /** Zero-sample privacy mode (#1676) — when true, no failing-row sample is ever persisted. */
  zero_sample_mode: boolean;
  external_transfers: ExternalTransfer[];
}

export async function getDeploymentPosture(): Promise<DeploymentPosture> {
  const { data } = await api.get<DeploymentPosture>('/admin/deployment');
  return data;
}

/** Audit hash-chain verification (ADR 0041 §9 / #1460). */
export interface AuditChainBreak {
  event_id: string;
  occurred_at: string | null;
  expected_prev_hash: string | null;
  actual_prev_hash: string | null;
}

export interface AuditChainStatus {
  /** `empty` (no hashed rows yet) is deliberately distinct from `ok`. */
  status: 'ok' | 'broken' | 'empty';
  verified_count: number;
  /** Rows written before the chain shipped — real history, just not covered by it. */
  unverifiable_legacy_count: number;
  chain_head_hash: string | null;
  /** `none` = internally consistent, but not independently verifiable (ADR 0041 §9). */
  anchor_mode: 'none' | 'webhook';
  first_break: AuditChainBreak | null;
}

export async function verifyAuditChain(): Promise<AuditChainStatus> {
  const { data } = await api.get<AuditChainStatus>('/admin/audit-events/verify');
  return data;
}

/** A data subject is identified the way the warehouse identifies them — a
 *  `(column, value)` pair. DataQ has no people-table (G2 / #432). */
export interface DataSubjectRequest {
  column: string;
  value: string;
}

export interface DataSubjectMatch {
  result_id: string;
  run_id: string;
  suite_id: string;
  suite_name: string;
  check_id: string;
  check_name: string;
  created_at: string;
  matched_in: string[];
  /** Unredacted by design — this endpoint IS the subject's own access right. */
  sample_failures: Record<string, unknown> | null;
  observed_value: Record<string, unknown> | null;
}

export interface DataSubjectIncidentMatch {
  incident_id: string;
  suite_id: string;
  suite_name: string;
  check_id: string;
  check_name: string;
  status: string;
  created_at: string;
  observed_value: Record<string, unknown> | null;
}

export interface DataSubjectExport extends DataSubjectRequest {
  /** Results + incident snapshots together; the two lists below say where. */
  match_count: number;
  matches: DataSubjectMatch[];
  incident_match_count: number;
  incident_matches: DataSubjectIncidentMatch[];
}

export interface DataSubjectErasure extends DataSubjectRequest {
  matched_count: number;
  erased_count: number;
  matched_result_count: number;
  erased_result_count: number;
  matched_incident_count: number;
  erased_incident_count: number;
}

export async function exportDataSubject(column: string, value: string): Promise<DataSubjectExport> {
  const { data } = await api.post<DataSubjectExport>('/admin/data-subject-requests/export', {
    column,
    value,
  });
  return data;
}

/** Erasure is surgical (only the matching row/cell) and irreversible. The typed
 *  confirmation is a UI guard — the endpoint takes no confirmation argument. */
export async function eraseDataSubject(column: string, value: string): Promise<DataSubjectErasure> {
  const { data } = await api.post<DataSubjectErasure>('/admin/data-subject-requests/erase', {
    column,
    value,
  });
  return data;
}
