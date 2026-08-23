import { Spin } from 'antd';
import type { ReactNode } from 'react';

import type { WorkspaceRole } from '../api/admin';
import { Forbidden } from '../components/Forbidden';
import { useWorkspaceRole } from './useMe';

/** Workspace-role ranks (ADR 0033) — mirrors the backend's `ROLE_RANK`. */
const RANK: Record<WorkspaceRole, number> = { viewer: 1, member: 2, admin: 3 };

/**
 * Route guard: render `children` only if the caller's workspace role is at least `minimum`,
 * otherwise the Forbidden page (ADR 0033, #743).
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
