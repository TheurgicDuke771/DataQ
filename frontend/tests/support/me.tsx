import type { ReactNode } from 'react';

import type { WorkspaceRole } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';

/**
 * Wrap a tree in a resolved `/me` at a given workspace role (ADR 0033, #743).
 *
 * Needed because `MeContext` defaults to `{ status: 'loading' }`, and the
 * role-aware pages treat "not yet known" as "render nothing role-dependent" —
 * deliberately, since showing a read-only shell during the fetch and then
 * popping controls in is worse than a beat of nothing. A page test that does not
 * provide `/me` therefore sees every gated control hidden, which is correct
 * behaviour and a useless test setup.
 *
 * `admin` is the default because it is what the pre-#743 tests were implicitly
 * exercising: the dev-bypass identity every page test stands in for IS a
 * workspace admin (#741). Pass another role to assert a restricted perspective.
 */
export function meAt(role: WorkspaceRole = 'admin'): MeResponse {
  return {
    id: 'me-1',
    aad_object_id: 'oid-1',
    email: `${role}@dataq.local`,
    display_name: 'Test User',
    last_seen_at: null,
    role,
    is_workspace_admin: role === 'admin',
  };
}

export function WithMe({
  role = 'admin',
  children,
}: {
  role?: WorkspaceRole;
  children: ReactNode;
}) {
  return (
    <MeContext.Provider value={{ status: 'ok', data: meAt(role) }}>{children}</MeContext.Provider>
  );
}
