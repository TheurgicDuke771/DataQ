import { Spin } from 'antd';
import type { ReactNode } from 'react';

import type { WorkspaceRole } from '../api/admin';
import { PageError } from '../components/feedback/PageError';
import { Forbidden } from '../components/Forbidden';
import { useMe } from './useMe';

/** Workspace-role ranks (ADR 0033) — mirrors the backend's `ROLE_RANK`. */
const RANK: Record<WorkspaceRole, number> = { viewer: 1, member: 2, admin: 3 };

/**
 * Route guard: render `children` only if the caller's workspace role is at least `minimum`,
 * otherwise the Forbidden page (ADR 0033, #743).
 *
 * A failed `/me` is neither "allowed" nor "denied" — it is unknown, and spinning forever on it
 * hides a real failure. Every gated route therefore gets the same classified error page the
 * pages behind it used to render for themselves (#910/#930).
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
  const me = useMe();

  if (me.status === 'error') {
    return (
      <PageError
        error={me.error}
        kind={me.kind}
        httpStatus={me.httpStatus}
        requestId={me.requestId}
      />
    );
  }
  if (me.status === 'loading') {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (RANK[me.data.role] < RANK[minimum]) {
    return <Forbidden message={message} />;
  }
  return <>{children}</>;
}
