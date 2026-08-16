import { Spin } from 'antd';
import type { ReactNode } from 'react';

import type { WorkspaceRole } from '../api/admin';
import { Forbidden } from '../components/Forbidden';
import { useWorkspaceRole } from './useMe';

/** Workspace-role ranks (ADR 0033) — mirrors the backend's `ROLE_RANK`. */
const RANK: Record<WorkspaceRole, number> = { viewer: 1, member: 2, admin: 3 };

/**
 * Route guard: render `children` only if the caller's workspace role is at least
 * `minimum`, otherwise the Forbidden page (ADR 0033, #743).
 *
 * Hiding the *button* that navigates here is not enough. A bookmark, a browser
 * Back after a demotion, a shared link, or simply typing the URL all reach these
 * routes directly — and without this a Member would fill in an entire connection
 * form, credential and all, only to learn at submit that the endpoint 403s. That
 * is a worse experience than the `/admin` page's honest Forbidden state, and it
 * asks the user to hand over a credential for a request that cannot succeed.
 *
 * Not security: the endpoints re-enforce (#741), and this component is client
 * code a determined caller can bypass. It is about telling the truth at the
 * earliest point we know it.
 *
 * While `/me` is unresolved the role is `null` — "not yet known", NOT "denied" —
 * so this renders a spinner rather than flashing Forbidden at a user who turns
 * out to be an admin.
 */
export function RequireRole({
  minimum,
  children,
  message,
}: {
  minimum: WorkspaceRole;
  children: ReactNode;
  message?: string;
}) {
  const role = useWorkspaceRole();

  if (role === null) {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (RANK[role] < RANK[minimum]) {
    return <Forbidden message={message} />;
  }
  return <>{children}</>;
}
