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

/** ── Workspace membership (ADR 0043) ─────────────────────────────────────────
 *  Who is admitted to the workspace, as opposed to what they can do once in.
 *  Adding a member does NOT create an account at the identity provider. */

/** How the row got there: a deliberate add, or the switch-on import awaiting review. */
export type MemberSource = 'admin' | 'auto_import';

export interface WorkspaceMember {
  id: string;
  email: string;
  initial_role: WorkspaceRole;
  source: MemberSource;
  invited_by_email: string | null;
  created_at: string;
  /** Set once this address has signed in at least once. */
  user_id: string | null;
  stored_role: WorkspaceRole | null;
  /** `pending` means admitted but never signed in — not a failure state. */
  status: 'active' | 'pending';
}

export interface MembershipView {
  /** False while the list is empty: who may sign in is then env config alone. */
  enforcement_active: boolean;
  /** Existing users the FIRST add would import as provisional members. */
  unmanaged_user_count: number;
  members: WorkspaceMember[];
}

export interface MemberAdded {
  member: WorkspaceMember;
  auto_imported_count: number;
  enforcement_active: boolean;
}

export async function listWorkspaceMembers(signal?: AbortSignal): Promise<MembershipView> {
  const { data } = await api.get<MembershipView>('/admin/members', { signal });
  return data;
}

export async function addWorkspaceMember(
  email: string,
  initialRole: WorkspaceRole,
): Promise<MemberAdded> {
  const { data } = await api.post<MemberAdded>('/admin/members', {
    email,
    initial_role: initialRole,
  });
  return data;
}

/** `confirmSelf` is required by the backend to remove your OWN membership. */
export async function removeWorkspaceMember(id: string, confirmSelf = false): Promise<void> {
  await api.delete(`/admin/members/${id}`, { params: { confirm_self: confirmSelf } });
}

/** Clear the provisional flag on an auto-imported row. Grants nothing new. */
export async function confirmWorkspaceMember(id: string): Promise<WorkspaceMember> {
  const { data } = await api.post<WorkspaceMember>(`/admin/members/${id}/confirm`);
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

/** The four Overview stat cards — workspace-wide counts, not the caller's grants. */
export interface AdminOverview {
  members: {
    total: number;
    /** `null` (never `0`) while DataQ has no invite record: a user row is created BY the
     *  first sign-in, so an admitted-but-never-signed-in person leaves no trace. */
    pending_first_signin: number | null;
    pending_source: 'not_available';
  };
  /** `connections` counts the DISTINCT connections suites target, not those configured. */
  suites: { total: number; connections: number };
  /** `acknowledged` is a SUBSET of `open` — acknowledging silences nothing. */
  incidents: { open: number; acknowledged: number };
  /** Since the start of the current UTC day (`since`); `total` also counts queued and
   *  cancelled runs, so the three named states need not sum to it. */
  runs_today: { total: number; succeeded: number; failed: number; running: number; since: string };
  generated_at: string;
}

export async function getAdminOverview(): Promise<AdminOverview> {
  const { data } = await api.get<AdminOverview>('/admin/overview');
  return data;
}

/** One orchestration connection's poll staleness. `unknown` = never polled at all, which
 *  is not healthy; `last_polled_at` is the last ATTEMPT, not the last success. */
export interface PollHealth {
  connection_id: string;
  name: string;
  provider: OrchestrationProvider;
  last_polled_at: string | null;
  cadence_seconds: number;
  next_expected_at: string | null;
  status: 'on_cadence' | 'stalled' | 'failing' | 'unknown';
  last_error: string | null;
}

/** `not_monitored` = the heartbeat has never recorded a tick, which is not `alive`. */
export interface BeatHealth {
  last_tick_at: string | null;
  status: 'alive' | 'stale' | 'not_monitored';
}

export interface QueueDepth {
  name: string;
  depth: number;
}

/** One datasource connection's stored-credential health. `unknown` = nothing observed since
 *  the signal shipped; only credential REJECTIONS move it off that. */
export interface CredentialHealth {
  connection_id: string;
  name: string;
  type: string;
  env: string;
  status: 'healthy' | 'failing' | 'unknown';
  consecutive_auth_failures: number;
  last_auth_failure_at: string | null;
  last_auth_success_at: string | null;
  last_error: string | null;
}

export interface AdminHealth {
  polling: PollHealth[];
  beat: BeatHealth;
  /** `null` (never a fake `0`) when the broker was unreachable — `queues_error` says why. */
  queues: QueueDepth[] | null;
  queues_error: string | null;
  credentials: CredentialHealth[];
  generated_at: string;
}

export async function getAdminHealth(): Promise<AdminHealth> {
  const { data } = await api.get<AdminHealth>('/admin/health');
  return data;
}

/** The last orphan-secret sweep. `never_run`/`skipped` must never read as `orphan_count: 0`;
 *  the counts are `null` on a skip or a store outage, and `error` then classifies it. */
export interface SecretSweepReport {
  status: 'never_run' | 'recorded' | 'skipped';
  ran_at: string | null;
  mode: 'report' | 'purge' | null;
  orphan_count: number | null;
  orphan_names: string[];
  truncated: boolean;
  scanned: number | null;
  unknown_age_count: number | null;
  too_young_count: number | null;
  store: string | null;
  error: string | null;
}

export async function getSecretSweep(): Promise<SecretSweepReport> {
  const { data } = await api.get<SecretSweepReport>('/admin/secret-sweep');
  return data;
}

/** Enqueues the sweep in report-only mode; its result lands in `getSecretSweep` later. */
export async function runSecretSweep(): Promise<{ status: string; task_id: string }> {
  const { data } = await api.post<{ status: string; task_id: string }>('/admin/secret-sweep/run');
  return data;
}

/** Webhook secret regeneration (#1701). `value` is shown once — no read returns it again;
 *  `grace_until` is when the previous value stops being accepted (`null` = already dead). */
export interface WebhookRegeneration {
  provider: OrchestrationProvider;
  secret_name: string;
  auth_mode: 'url_token' | 'hmac';
  value: string;
  grace_until: string | null;
  inbound_url: string | null;
}

export async function regenerateWebhookSecret(
  provider: OrchestrationProvider,
): Promise<WebhookRegeneration> {
  const { data } = await api.post<WebhookRegeneration>(
    `/admin/orchestration/webhooks/${provider}/regenerate`,
  );
  return data;
}

export interface PollDispatch {
  provider: OrchestrationProvider;
  connection_id: string | null;
  /** `provider` = the sweep covered every connection of that provider, not one. */
  scope: 'provider' | 'connection';
  task_id: string;
}

export interface PollNowResult {
  /** Empty with no error = there were no orchestration connections to poll. */
  dispatched: PollDispatch[];
  requested_at: string;
}

export async function pollNow(connectionId?: string): Promise<PollNowResult> {
  const { data } = await api.post<PollNowResult>('/admin/orchestration/poll-now', undefined, {
    params: connectionId ? { connection_id: connectionId } : undefined,
  });
  return data;
}

/** `never_synced` is not `0 tables` — the counts are `null` until a sync has run. */
export interface InventorySyncRow {
  connection_id: string;
  name: string;
  type: string;
  env: string;
  enabled: boolean;
  last_attempted_at: string | null;
  failing_since: string | null;
  last_error: string | null;
  tables_discovered: number | null;
  unmonitored: number | null;
  status: 'never_synced' | 'synced' | 'failing';
}

export async function listInventorySync(): Promise<InventorySyncRow[]> {
  const { data } = await api.get<InventorySyncRow[]>('/admin/inventory-sync');
  return data;
}

export async function setInventorySync(
  connectionId: string,
  enabled: boolean,
): Promise<InventorySyncRow> {
  const { data } = await api.patch<InventorySyncRow>(`/admin/inventory-sync/${connectionId}`, {
    enabled,
  });
  return data;
}

export async function runInventorySync(
  connectionId: string,
): Promise<{ status: string; task_id: string }> {
  const { data } = await api.post<{ status: string; task_id: string }>(
    `/admin/inventory-sync/${connectionId}/run`,
  );
  return data;
}
